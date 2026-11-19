from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from decimal import Decimal
from app.values import instant,number,money
from app.errors import DomainError
HOLIDAYS={"JP":{"2026-11-03","2026-11-23"},"US":{"2026-11-26"},"DE":{"2026-10-03"}}
def working_hours(start,end,zone,holidays):
    if end<start:raise DomainError("NEGATIVE_INTERVAL","妥投早于收件")
    tz=ZoneInfo(zone);cursor=start;seconds=Decimal(0)
    while cursor<end:
        local=cursor.astimezone(tz);next_day=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=tz).astimezone(cursor.tzinfo)
        stop=min(end,next_day)
        if local.weekday()<5 and local.date().isoformat() not in holidays:seconds+=Decimal(str((stop-cursor).total_seconds()))
        cursor=stop
    return seconds/3600
def evaluate(rows,context):
    p=context.get("parameters",{});as_of=instant(p.get("as_of","2026-11-18T00:00:00Z"));rate=number(p.get("hourly_compensation","1.00"))
    if rate<0:raise DomainError("BAD_RATE","赔付费率不能为负")
    good=[];settlements=[]
    for index,row in enumerate(rows):
        # 每个运输实例各用本次启用的收件与妥投时钟；旧实例签收不参与新实例。
        key=str(row.get("instance_id") or row.get("parcel") or row.get("_line") or index)
        start=instant(row["accepted_at"]);end=instant(row["delivered_at"]) if row.get("delivered_at") else max(start,as_of)
        hours=working_hours(start,end,row["zone"],set(p.get("holidays",{}).get(row["destination"],HOLIDAYS.get(row["destination"],set()))))
        late=max(Decimal(0),hours-number(row["promised_hours"]));comp=min(number(row["declared_value"])*Decimal("0.10"),late*rate)
        settlements.append(key)
        official=bool(row.get("current",True))
        # 历史实例只保留参考值，本次不再计赔；每个面单只在最新实例上产生唯一正式赔付。
        good.append({**row,"settlement_id":key,"settlement_role":"正式" if official else "历史",
                     "working_hours":str(hours),"late_hours":str(late),
                     "expected":money(comp) if official else "0.00",
                     "reference_expected":money(comp),
                     "reported":"0.00" if not official else money(row["reported"]) if "reported" in row else row.get("reported","0"),
                     "reference_reported":money(row["reported"]) if "reported" in row else "0.00",
                     "settlement_note":"本次正式赔付" if official else "历史实例，不重复计赔，原签收单据仅作证据",
                     "sla":"超时" if late>0 else "达标","clock_end":end.isoformat()})
    if len(settlements)!=len(set(settlements)):raise DomainError("DUPLICATE_SETTLEMENT","同一运输实例被重复结算")
    return {"rows":good,"quarantine":[],"metrics":{"parcels":len(good),"instances":len(set(settlements)),"settlements":len(set(settlements)),
            "official":sum(r["settlement_role"]=="正式" for r in good),
            "late_parcels":sum(r["sla"]=="超时" and r["settlement_role"]=="正式" for r in good),
            "compensation":money(sum((number(r["expected"]) for r in good),Decimal(0))),
            "official_compensation":money(sum((number(r["expected"]) for r in good if r["settlement_role"]=="正式"),Decimal(0))),
            "historical_reference":money(sum((number(r["reference_expected"]) for r in good if r["settlement_role"]!="正式"),Decimal(0)))}}
