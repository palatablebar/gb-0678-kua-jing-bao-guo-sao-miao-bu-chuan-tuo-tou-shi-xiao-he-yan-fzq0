"""同一面单多次启用：实例拆分、唯一正式妥投与赔付、历史证据与归档版本。"""
import csv,io,json
import pytest
from decimal import Decimal
from app.errors import DomainError
from app.modules.m4_paths.rules import evaluate as paths,evaluate as path_stage
from app.modules.m5_timeliness.rules import evaluate as timeliness
from app.modules.m6_compensation.rules import evaluate as compensation
from app.modules.m7_archives.rules import evaluate as archives
from app.pipeline import calculate
from app import store

HEADER="scan_id,parcel,node,event_time,received_time,destination,promised_hours,declared_value,reported,note\n"

def row(scan_id,node,event,received="2026-11-01T00:00:00Z",parcel="0001",destination="JP",promised="24",value="200",reported="0",note="test"):
    return [scan_id,parcel,node,event,received,destination,promised,value,reported,note]

def csv_text(*rows):
    return HEADER+"".join(",".join(r)+"\n" for r in rows)

def stage_rows(scan_id,node,at,parcel="0001",received="2026-11-09T00:00:00Z",node_code=None):
    return {"scan_id":str(scan_id),"parcel":parcel,"node":node_code or node,"event_utc":at,"received_utc":received,
            "zone":"Asia/Tokyo","destination":"JP","promised_hours":"24","declared_value":"200","reported":"0","_sources":[scan_id]}

def run_stage3(*rows):
    return paths(list(rows),{})

def reused(extra=()):
    return run_stage3(
        stage_rows(1,"ACCEPT","2026-11-02T00:00:00Z"),
        stage_rows(2,"DEPART","2026-11-02T12:00:00Z"),
        stage_rows(3,"DELIVERED","2026-11-04T00:00:00Z"),
        stage_rows(10,"ACCEPT","2026-11-08T00:00:00Z",received="2026-11-08T01:00:00Z"),
        stage_rows(11,"DEPART","2026-11-09T00:00:00Z"),
        stage_rows(12,"CUSTOMS","2026-11-10T00:00:00Z"),
        stage_rows(13,"DELIVERED","2026-11-12T00:00:00Z"),*extra)

def test_reuse_splits_into_two_instances():
    r=reused()
    assert [x["instance_id"] for x in r["rows"]]==["0001#1","0001#2"]
    assert r["metrics"]["instances"]==2 and r["metrics"]["reuses"]==1
    assert [x["current"] for x in r["rows"]]==[False,True]

def test_old_delivery_does_not_end_new_instance():
    first,second=reused()["rows"]
    assert first["delivered_at"]=="2026-11-04T00:00:00Z"
    assert second["accepted_at"]=="2026-11-08T00:00:00Z" and second["delivered_at"]=="2026-11-12T00:00:00Z"
    assert second["prior_delivery_at"]=="2026-11-04T00:00:00Z" and second["prior_merged"] is False
    assert "不并入" in second["segment_reason"]

def test_old_delivery_evidence_is_kept():
    first,second=reused()["rows"]
    assert first["_sources"]==[1,2,3]
    assert second["prior_delivery_sources"]==[3]
    assert second["_sources"]==[10,11,12,13]

def test_reuse_without_legacy_regression():
    r=reused()
    assert not any(x["reason"]=="PATH_REGRESSION" for x in r["quarantine"])
    assert r["metrics"]["path_errors"]==0

def test_return_then_reuse():
    r=run_stage3(
        stage_rows(1,"ACCEPT","2026-11-02T00:00:00Z"),
        stage_rows(2,"RETURN","2026-11-03T00:00:00Z"),
        stage_rows(3,"ACCEPT","2026-11-05T00:00:00Z"),
        stage_rows(4,"DELIVERED","2026-11-06T00:00:00Z"))
    assert [x["instance_id"] for x in r["rows"]]==["0001#1","0001#2"]
    assert r["rows"][0]["returned_at"]=="2026-11-03T00:00:00Z"
    assert r["rows"][1]["prior_delivery_at"] is None and r["metrics"]["reuses"]==1

def test_duplicate_delivery_same_instance_quarantined():
    r=run_stage3(
        stage_rows(1,"ACCEPT","2026-11-02T00:00:00Z"),
        stage_rows(2,"DELIVERED","2026-11-04T00:00:00Z"),
        stage_rows(3,"DELIVERED","2026-11-05T00:00:00Z"))
    assert len(r["rows"])==1 and r["rows"][0]["delivered_at"]=="2026-11-04T00:00:00Z"
    assert r["quarantine"][0]["reason"]=="DUPLICATE_DELIVERY"

def test_retransmitted_scan_does_not_open_instance():
    retry=stage_rows("10r","ACCEPT","2026-11-08T00:00:00Z",received="2026-11-08T02:00:00Z")
    r=reused([retry])
    assert r["metrics"]["instances"]==2 and r["metrics"]["reuses"]==1
    assert r["rows"][1]["_sources"]==[10,11,12,13]
    assert r["quarantine"][0]["reason"]=="DUPLICATE_SCAN_EVENT"

def test_duplicate_accept_inside_instance_not_new_instance():
    r=run_stage3(
        stage_rows(1,"ACCEPT","2026-11-02T00:00:00Z"),
        stage_rows(2,"ACCEPT","2026-11-02T06:00:00Z"),
        stage_rows(3,"DELIVERED","2026-11-04T00:00:00Z"))
    assert r["metrics"]["instances"]==1
    assert r["quarantine"][0]["reason"]=="DUPLICATE_ACCEPT"

def test_order_independent_for_reuse():
    forward=reused()
    rows=list(reversed([stage_rows(1,"ACCEPT","2026-11-02T00:00:00Z"),stage_rows(2,"DEPART","2026-11-02T12:00:00Z"),
        stage_rows(3,"DELIVERED","2026-11-04T00:00:00Z"),stage_rows(10,"ACCEPT","2026-11-08T00:00:00Z",received="2026-11-08T01:00:00Z"),
        stage_rows(11,"DEPART","2026-11-09T00:00:00Z"),stage_rows(12,"CUSTOMS","2026-11-10T00:00:00Z"),
        stage_rows(13,"DELIVERED","2026-11-12T00:00:00Z")]))
    assert run_stage3(*rows)==forward

def test_only_current_instance_is_official():
    stage3=reused()
    settled=timeliness(stage3["rows"],{})
    roles={r["instance_id"]:r["settlement_role"] for r in settled["rows"]}
    assert roles=={"0001#1":"历史","0001#2":"正式"}
    assert settled["metrics"]["official"]==1 and settled["metrics"]["official_compensation"]==settled["rows"][1]["expected"]
    assert settled["rows"][0]["expected"]=="0.00" and settled["rows"][0]["reference_expected"]==settled["rows"][0]["reference_expected"]
    assert Decimal(settled["metrics"]["compensation"])==Decimal(settled["rows"][1]["expected"])

def test_new_instance_compensated_with_its_own_clock():
    stage3=reused()
    settled={r["instance_id"]:r for r in timeliness(stage3["rows"],{})["rows"]}
    assert settled["0001#2"]["accepted_at"]=="2026-11-08T00:00:00Z"
    assert Decimal(settled["0001#2"]["late_hours"])>0
    assert settled["0001#2"]["expected"]!="0.00"

def test_old_delivery_does_not_offset_new_compensation():
    stage3=reused()
    timeline=timeliness(stage3["rows"],{})["rows"]
    reconciled=compensation(timeline,{"quarantine":stage3["quarantine"]})
    official=[r for r in reconciled["rows"] if r["settlement_role"]=="正式"]
    assert len(official)==1 and official[0]["instance_id"]=="0001#2"
    assert Decimal(reconciled["metrics"]["official_expected"])==Decimal(official[0]["expected"])
    assert reconciled["metrics"]["official_records"]==1

def test_historical_row_never_reports_pass_or_fail():
    stage3=reused()
    reconciled=compensation(timeliness(stage3["rows"],{})["rows"],{"quarantine":[]})
    historical=[r for r in reconciled["rows"] if r["settlement_role"]=="历史"]
    assert all(r["status"]=="历史" for r in historical)

def test_duplicate_settlement_rejected():
    stage3=reused();timeline=timeliness(stage3["rows"],{})["rows"]
    with pytest.raises(DomainError) as e:compensation(timeline+[timeline[-1]],{"quarantine":[]})
    assert e.value.code=="DUPLICATE_SETTLEMENT"

def test_archive_requires_one_official_per_parcel():
    stage3=reused();timeline=timeliness(stage3["rows"],{})
    stripped=[{**r,"settlement_role":"历史"} for r in timeline["rows"]]
    with pytest.raises(DomainError) as e:archives(stripped,{})
    assert e.value.code=="OFFICIAL_SETTLEMENT"
    sealed=archives(timeline["rows"],{})
    assert sealed["metrics"]["ready"] is True and sealed["metrics"]["official_settlements"]==1

def test_full_pipeline_reuse_end_to_end():
    raw=csv_text(
        row("0010","收件","2026-11-02T00:00:00Z",received="2026-11-02T01:00:00Z"),
        row("0011","起运","2026-11-02T12:00:00Z",received="2026-11-02T13:00:00Z"),
        row("0013","妥投","2026-11-04T00:00:00Z",received="2026-11-06T00:00:00Z",reported="5.00"),
        row("1010","收件","2026-11-08T00:00:00Z",received="2026-11-08T01:00:00Z"),
        row("1011","起运","2026-11-09T00:00:00Z",received="2026-11-09T01:00:00Z"),
        row("1012","清关","2026-11-10T00:00:00Z",received="2026-11-10T01:00:00Z"),
        row("1013","妥投","2026-11-12T00:00:00Z",received="2026-11-12T01:00:00Z"),
        row("1010","收件","2026-11-08T00:00:00Z",received="2026-11-08T02:00:00Z",note="retry")).encode()
    run=calculate(raw)
    path=run["stages"][3];settle=run["stages"][4];money=run["stages"][5];seal=run["stages"][6]
    assert path["metrics"]["instances"]==2 and path["metrics"]["reuses"]==1
    assert [r["settlement_role"] for r in settle["rows"]]==["历史","正式"]
    assert money["metrics"]["balanced"] is True and money["metrics"]["official_records"]==1
    assert seal["metrics"]["official_settlements"]==1 and seal["metrics"]["historical_instances"]==1
    assert run["run_id"]==calculate(raw)["run_id"]
    assert len(store.history())==0
