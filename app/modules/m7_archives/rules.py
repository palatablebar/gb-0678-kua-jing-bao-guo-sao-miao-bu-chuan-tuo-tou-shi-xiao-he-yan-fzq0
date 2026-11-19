import hashlib,json
from decimal import Decimal
from app.errors import DomainError
from app.reasons import sources
def evaluate(rows,context):
    stages=[v for v in context.values() if isinstance(v,dict) and "metrics" in v]
    balances=[s["metrics"]["balanced"] for s in stages if "balanced" in s["metrics"]]
    if balances and not all(balances): raise DomainError("UNBALANCED","来源行未对平，不能归档")
    ids=[str(r.get("instance_id") or r.get("parcel",r.get("_line",""))) for r in rows]
    if len(ids)!=len(set(ids)): raise DomainError("ARCHIVE_DUPLICATE","归档实例键重复")
    settled=[str(r.get("settlement_id") or r.get("instance_id") or r.get("parcel")) for r in rows]
    if len(settled)!=len(set(settled)): raise DomainError("DUPLICATE_SETTLEMENT","同一运输实例被重复结算")
    official={}
    for row in rows:
        if row.get("settlement_role","正式")=="正式":
            official[str(row.get("parcel"))]=official.get(str(row.get("parcel")),0)+1
    parcels={str(row.get("parcel")) for row in rows}
    if set(official)!=parcels or any(count!=1 for count in official.values()): raise DomainError("OFFICIAL_SETTLEMENT","每个面单须恰有一个正式结算实例")
    official_count=sum(official.values())
    encoded=json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()
    return {"rows":rows,"quarantine":[],"metrics":{"records":len(rows),"source_lines":len(set(x for row in rows for x in sources(row))),
            "sha256":hashlib.sha256(encoded).hexdigest(),"ready":True,
            "official_settlements":official_count,"historical_instances":len(rows)-official_count,
            "differences":sum(r.get("status_detail",r.get("status"))=="差异" for r in rows)}}
