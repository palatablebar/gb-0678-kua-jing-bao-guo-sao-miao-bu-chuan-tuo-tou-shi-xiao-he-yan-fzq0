from collections import defaultdict
from app.values import instant
from app.reasons import reject,sources
PRIORITY={"ACCEPT":0,"DEPART":1,"CUSTOMS":2,"DELIVERED":3,"RETURN":4}

def canonical(ordered):
    """同一面单同一节点同一发生时刻只采纳一次扫描。

    补传/重试常以新的 scan_id 重发同一事件；以最早上传（再按 scan_id）
    的扫描为正式扫描，其余行进入隔离并保留为历史证据，不据此另开运输实例。
    """
    keepers={};unique=[];duplicates=[]
    for event in ordered:
        key=(event["node"],instant(event["event_utc"]))
        if key not in keepers:
            keepers[key]=event;unique.append(event);continue
        current=keepers[key]
        if (instant(event["received_utc"]),event["scan_id"])<(instant(current["received_utc"]),current["scan_id"]):
            unique[unique.index(current)]=event;keepers[key]=event
            duplicates.append((current,event))
        else:duplicates.append((event,current))
    unique.sort(key=lambda r:(instant(r["event_utc"]),PRIORITY[r["node"]],r["scan_id"]))
    return unique,duplicates

def split_instances(parcel,events,bad):
    """按面单每次启用拆成独立运输实例。

    边界：实例已取得正式妥投或退运后再次出现收件扫描，即面单重新启用。
    上一实例的签收只挂在链上作历史证据，绝不并入或结束新实例。
    """
    instances=[];current=None;reuses=0
    for event in events:
        node=event["node"];time=event["event_utc"]
        if node=="ACCEPT" and (current is None or current["delivered"] or current["returned"]):
            if current is not None:reuses+=1
            current={"events":[],"accepts":[],"delivered":None,"returned":None,"last":None,"seen":{},
                     "prior_delivery":current["delivered"] if current else None,
                     "prior_return":current["returned"] if current else None,
                     "prior_instance_no":current["instance_no"] if current else None,
                     "instance_no":len(instances)+1}
            current["instance_id"]="%s#%d"%(parcel,current["instance_no"])
            instances.append(current)
        if current is None:
            bad.append(reject(event,"MISSING_ACCEPT","缺少收件扫描"));continue
        if node=="ACCEPT" and current["accepts"]:
            bad.append(reject(event,"DUPLICATE_ACCEPT","同一运输实例重复收件；不另开实例"));continue
        if node=="DELIVERED" and current["delivered"]:
            bad.append(reject(event,"DUPLICATE_DELIVERY","同一运输实例已有正式妥投"));continue
        if node=="RETURN" and current["returned"]:
            bad.append(reject(event,"DUPLICATE_RETURN","同一运输实例已有退运扫描"));continue
        if current["last"] is not None and PRIORITY[node]<current["last"]:
            bad.append(reject(event,"PATH_REGRESSION","扫描路径倒退"));continue
        if time in current["seen"] and current["seen"][time]!=node:
            bad.append(reject(event,"SIMULTANEOUS_CONFLICT","同一时刻已有 %s 扫描"%current["seen"][time]));continue
        current["seen"][time]=node;current["last"]=PRIORITY[node];current["events"].append(event)
        if node=="ACCEPT":current["accepts"].append(event)
        if node=="DELIVERED":current["delivered"]=event
        if node=="RETURN":current["returned"]=event
    return instances,reuses

def materialize(inst,parcel):
    first=inst["accepts"][0];delivery=inst["delivered"];returned=inst["returned"];prior=inst["prior_delivery"]
    lineage=[]
    for event in inst["events"]:
        for line in sources(event):
            if line not in lineage:lineage.append(line)
    if delivery and returned:state="已妥投并退运"
    elif delivery:state="已妥投"
    elif returned:state="已退运"
    else:state="运输中"
    if prior:
        reason="妥投后重新启用；上一签收（%s，扫描 %s）仅作历史证据，不并入本实例，也不结束本实例"%(
            prior["event_utc"],prior["scan_id"])
    elif inst["prior_return"]:
        reason="退运后重新启用；上一实例无正式妥投，退运扫描仅作历史证据"
    else:reason="首次启用"
    return {**first,
            "_sources":lineage,
            "parcel":parcel,
            "instance_id":inst["instance_id"],
            "instance_no":inst["instance_no"],
            "instance_status":state,
            "opening_scan_id":first["scan_id"],
            "accepted_at":first["event_utc"],
            "delivered_at":delivery["event_utc"] if delivery else None,
            "delivery_uploaded_at":delivery["received_utc"] if delivery else None,
            "delivery_scan_id":delivery["scan_id"] if delivery else None,
            "returned_at":returned["event_utc"] if returned else None,
            "return_scan_id":returned["scan_id"] if returned else None,
            "scan_count":len(inst["events"]),
            "path":" → ".join(event["node"] for event in inst["events"]),
            "reported":delivery["reported"] if delivery else first["reported"],
            "prior_delivery_at":prior["event_utc"] if prior else None,
            "prior_delivery_scan_id":prior["scan_id"] if prior else None,
            "prior_delivery_sources":sources(prior) if prior else [],
            "prior_instance_id":"%s#%d"%(parcel,inst["prior_instance_no"]) if inst["prior_instance_no"] else None,
            "prior_merged":False,
            "segment_reason":reason,
            "chain":" → ".join("%s#%d"%(parcel,n) for n in range(1,inst["instance_no"]+1)),
            "current":False}

def evaluate(rows,context):
    groups=defaultdict(list);bad=[]
    for row in rows:groups[row["parcel"]].append(row)
    good=[];reuses=0;reused=set();duplicates=0
    for parcel,events in sorted(groups.items()):
        ordered=sorted(events,key=lambda r:(instant(r["event_utc"]),PRIORITY[r["node"]],r["scan_id"]))
        ordered,repeated=canonical(ordered)
        for dropped,kept in repeated:
            bad.append(reject(dropped,"DUPLICATE_SCAN_EVENT",f"重复扫描事件；采纳扫描 {kept['scan_id']}，本行保留为历史证据"))
        duplicates+=len(repeated)
        instances,count=split_instances(parcel,ordered,bad)
        reuses+=count
        if count:reused.add(parcel)
        materialized=[]
        for inst in instances:
            if not inst["accepts"]:
                bad.extend(reject(e,"MISSING_ACCEPT","缺少收件扫描") for e in inst["events"]);continue
            materialized.append(materialize(inst,parcel))
        if materialized:materialized[-1]["current"]=True
        good.extend(materialized)
    good.sort(key=lambda r:(r["parcel"],r["instance_no"]))
    return {"rows":good,"quarantine":bad,
            "metrics":{"parcels":len(groups),"instances":len(good),
                       "delivered":sum(r["delivered_at"] is not None for r in good),
                       "in_transit":sum(r["delivered_at"] is None for r in good),
                       "path_errors":len(bad),"reuses":reuses,
                       "reused_parcels":len(reused),"duplicate_events":duplicates}}
