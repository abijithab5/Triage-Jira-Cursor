import re
from datetime import datetime
from zoneinfo import ZoneInfo

tz_utc = ZoneInfo("UTC")
tz_cet = ZoneInfo("Europe/Berlin")

def convert_to_cet(match, year_hint=2026):
    ts_str = match.group(0)
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$', ts_str):
            fmt = "%Y-%m-%dT%H:%M:%S" if 'T' in ts_str else "%Y-%m-%d %H:%M:%S"
            dt = datetime.strptime(ts_str, fmt)
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            return dt.strftime(fmt)
            
        elif re.match(r'^\d{6}-\d{2}:\d{2}:\d{2}$', ts_str):
            dt = datetime.strptime(ts_str, "%y%m%d-%H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            return dt.strftime("%y%m%d-%H:%M:%S")
            
        elif re.match(r'^[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}$', ts_str):
            dt_str = f"{year_hint} {ts_str}"
            dt_str_normalized = re.sub(r'\s+', ' ', dt_str)
            dt = datetime.strptime(dt_str_normalized, "%Y %b %d %H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            day_str = str(dt.day).rjust(2, ' ')
            return dt.strftime(f"%b {day_str} %H:%M:%S")
            
        elif re.match(r'^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$', ts_str):
            dt = datetime.strptime(ts_str, "%Y-%m-%d-%H-%M-%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            return dt.strftime("%Y-%m-%d-%H-%M-%S")
            
        elif re.match(r'^\d{14}$', ts_str):
            dt = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            return dt.strftime("%Y%m%d%H%M%S")
            
        elif re.match(r'^\d{8}_\d{6}$', ts_str):
            dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            return dt.strftime("%Y%m%d_%H%M%S")
            
    except Exception:
        return ts_str
    return ts_str

pattern = re.compile(
    r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\b|'
    r'\b\d{6}-\d{2}:\d{2}:\d{2}\b|'
    r'\b[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\b|'
    r'\b\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\b|'
    r'\b\d{14}\b|'
    r'\b\d{8}_\d{6}\b'
)

print(pattern.sub(convert_to_cet, "****Merging 2026-04-08-10-49-28_zebra.log **********"))
print(pattern.sub(convert_to_cet, "****Merging 20260408104928_zebra.log **********"))
print(pattern.sub(convert_to_cet, "****Merging 20260408_104928_zebra.log **********"))
