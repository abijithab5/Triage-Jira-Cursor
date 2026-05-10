import re
from datetime import datetime
from zoneinfo import ZoneInfo

tz_utc = ZoneInfo("UTC")
tz_cet = ZoneInfo("Europe/Berlin")

def convert_to_cet(match, year_hint=2026):
    ts_str = match.group(0)
    try:
        if ts_str[4] == '-':
            if 'T' in ts_str:
                dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") + ts_str[19:]
            else:
                dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
                return dt.strftime("%Y-%m-%d %H:%M:%S") + ts_str[19:]
        elif ts_str[6] == '-':
            dt = datetime.strptime(ts_str[:15], "%y%m%d-%H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            return dt.strftime("%y%m%d-%H:%M:%S") + ts_str[15:]
        else:
            dt_str = f"{year_hint} {ts_str}"
            dt_str_normalized = re.sub(r'\s+', ' ', dt_str)
            dt = datetime.strptime(dt_str_normalized, "%Y %b %d %H:%M:%S")
            dt = dt.replace(tzinfo=tz_utc).astimezone(tz_cet)
            day_str = str(dt.day).rjust(2, ' ')
            return dt.strftime(f"%b {day_str} %H:%M:%S")
    except Exception:
        return ts_str

pattern = re.compile(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\b|\b\d{6}-\d{2}:\d{2}:\d{2}\b|\b[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\b')

print(pattern.sub(convert_to_cet, "2026-04-08 12:44:55.829 [INFO]"))
