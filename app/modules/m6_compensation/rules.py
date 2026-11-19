from decimal import Decimal
from app.reasons import sources
from app.errors import DomainError

def evaluate(rows,context):
    intake=context.get("intake")
    bad=context.get("quarantine",[])
    accepted=set(x for row in rows for x in sources(row))
    rejected=set(x for row in bad for x in sources(row))
    if accepted & rejected: raise DomainError("LINEAGE_OVERLAP","同一来源行被重复裁决")
    m={"accepted_inputs":len(accepted),"quarantine_inputs":len(rejected),"output_records":len(rows)}
    if intake:
        excluded=intake["duplicate"]+intake["blank"]+intake["repeated_headers"]
        m.update(input=intake["input"],excluded=excluded,balanced=intake["input"]==len(accepted)+len(rejected)+excluded)
    else:m.update(input=len(accepted)+len(rejected),excluded=0,balanced=True)
    output=[];seen=set()
    for row in rows:
        key=str(row.get("settlement_id") or row.get("instance_id") or row.get("parcel"))
        # 同一运输实例只能有一个正式结算；重复补传不得再计一次赔。
        if key in seen:raise DomainError("DUPLICATE_SETTLEMENT","同一运输实例被重复结算")
        seen.add(key)
        expected=Decimal(str(row.get("expected","0")));reported=Decimal(str(row.get("reported","0")))
        role=row.get("settlement_role","正式")
        detail="历史" if role!="正式" else ("通过" if expected==reported else "差异")
        output.append({**row,"settlement_role":role,"difference":str(expected-reported),
                       "status":"历史" if role!="正式" else detail,"status_detail":detail})
    official=[r for r in output if r["settlement_role"]=="正式"]
    historical=[r for r in output if r["settlement_role"]!="正式"]
    m.update(expected_total=str(sum((Decimal(str(r.get("expected","0"))) for r in output),Decimal(0))),
             reported_total=str(sum((Decimal(str(r.get("reported","0"))) for r in output),Decimal(0))),
             official_expected=str(sum((Decimal(str(r.get("expected","0"))) for r in official),Decimal(0))),
             official_reported=str(sum((Decimal(str(r.get("reported","0"))) for r in official),Decimal(0))),
             official_records=len(official),historical_records=len(historical),
             official_differences=sum(r["status_detail"]=="差异" for r in official),
             differences=sum(r["status_detail"]=="差异" for r in official))
    return {"rows":output,"quarantine":[],"metrics":m}
