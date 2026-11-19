"""证据归档：幂等发布、失败保留旧版、历史版本可读。"""
import csv,io
import pytest
from app import store
from app.errors import DomainError
from app.pipeline import execute

REUSE=open("sample_data/reuse.csv","rb").read()

@pytest.fixture
def run(client):
    return client.post("/api/runs",json={"csv":REUSE.decode()}).get_json()

def test_publish_is_idempotent(client,run):
    first=client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1}).get_json()
    second=client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1}).get_json()
    assert first["reused"] is False and second["reused"] is True
    assert first["version"]==second["version"] and first["sha256"]==second["sha256"]
    versions=client.get("/api/runs/%s/archives"%run["run_id"]).get_json()["versions"]
    assert len(versions)==1 and versions[0]["state"]=="published"

def test_retry_does_not_create_second_run_or_archive(client,run):
    for _ in range(3):client.post("/api/runs",json={"csv":REUSE.decode()})
    for _ in range(3):client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1})
    assert len(store.history())==1
    assert len(client.get("/api/runs/%s/archives"%run["run_id"]).get_json()["versions"])==1

def test_stale_publish_rejected_and_old_version_kept(client,run):
    client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1})
    client.post("/api/runs/%s/review"%run["run_id"],json={"version":1,"decision":"已复核"})
    assert client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1}).status_code==409
    versions=client.get("/api/runs/%s/archives"%run["run_id"]).get_json()["versions"]
    assert [(v["version"],v["state"]) for v in versions]==[(1,"published")]

def test_failed_render_keeps_old_version(client,run,monkeypatch):
    client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1})
    old=client.get("/api/runs/%s/archives/1"%run["run_id"]).data
    def boom(version,published_at): raise RuntimeError("磁盘写入失败")
    with pytest.raises(RuntimeError):
        store.publish_archive(run["run_id"],1,"changed-evidence",boom)
    versions=client.get("/api/runs/%s/archives"%run["run_id"]).get_json()["versions"]
    assert [(v["version"],v["state"]) for v in versions]==[(1,"published")]
    assert store.published_archive(run["run_id"])["content"].encode()==old
    assert client.get("/api/runs/%s/archive"%run["run_id"]).status_code==200

def test_published_version_stays_readable_after_supersede(client,run):
    first=client.post("/api/runs/%s/archive"%run["run_id"],json={"version":1}).get_json()
    old=client.get("/api/runs/%s/archives/1"%run["run_id"]).data
    second=store.publish_archive(run["run_id"],1,"new,evidence\n1,2\n",lambda v,at:f"v{v}\n")
    assert second["version"]==2 and second["reused"] is False
    third=store.publish_archive(run["run_id"],1,"new,evidence\n1,2\n",lambda v,at:f"v{v}\n")
    assert third["reused"] is True and third["version"]==2
    versions=client.get("/api/runs/%s/archives"%run["run_id"]).get_json()["versions"]
    assert {v["version"]:v["state"] for v in versions}=={2:"published",1:"superseded"}
    assert client.get("/api/runs/%s/archives/1"%run["run_id"]).data==old

def test_archive_keeps_prior_delivery_and_quarantine(client,run):
    r=client.get("/api/runs/%s/archive"%run["run_id"])
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    kinds=[x[3] for x in rows[1:]]
    assert "prior_delivery" in kinds and "quarantine" in kinds
    prior=[x for x in rows[1:] if x[3]=="prior_delivery"][0]
    assert prior[4]=="0001#1" and "历史证据" in prior[6]
    duplicate=[x for x in rows[1:] if x[3]=="quarantine" and x[6]=="DUPLICATE_SCAN_EVENT"]
    assert duplicate and duplicate[0][5]=="[11]"

def test_archive_download_publishes_only_once(client,run):
    a=client.get("/api/runs/%s/archive"%run["run_id"])
    b=client.get("/api/runs/%s/archive"%run["run_id"])
    assert a.headers["X-Archive-Version"]==b.headers["X-Archive-Version"]=="1"
    assert a.data==b.data
