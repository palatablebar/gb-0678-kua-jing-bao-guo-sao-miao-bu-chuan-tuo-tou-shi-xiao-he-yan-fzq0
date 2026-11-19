import csv,io,json

def build_archive(run,archive_version=None,published_at=None):
    """导出唯一证据CSV。

    每个运输实例单独一行；被拆分出去的旧签收以 prior_delivery 行保留其源行与原因，
    历史证据不因归档而删除。版本与发布时间取自发布记录，而非当前批次状态。
    """
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","instance_id","source_lines","reason","evidence","archive_version","published_at"])
    meta=run["manifest"];rows=run["stages"][-1]["rows"]
    for row in rows:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",row.get("instance_id",""),json.dumps(row.get("_sources",[])),row.get("segment_reason",""),json.dumps(row,ensure_ascii=False),archive_version or "",published_at or ""])
        if row.get("prior_delivery_sources"):
            writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"prior_delivery",row.get("prior_instance_id",""),json.dumps(row["prior_delivery_sources"]),"上一实例签收，仅作历史证据，不并入本实例",json.dumps({k:row.get(k) for k in ("parcel","prior_instance_id","prior_delivery_at","prior_delivery_scan_id")},ensure_ascii=False),archive_version or "",published_at or ""])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine","",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False),archive_version or "",published_at or ""])
    return "﻿"+stream.getvalue()
