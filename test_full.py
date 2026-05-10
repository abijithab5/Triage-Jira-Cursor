from jira_triage.log_merger import convert_timestamps_to_cet

lines = [
    "2026-03-25T17:00:19 telekom: W019-1 WIFI.INFO",
    "******************** LOG_MERGE_MARKER: 2026-04-08 11:01:35 ********************",
    "260409-01:19:19 [ERROR] something",
    "Apr  8 10:50:17 airties: info",
    "Apr 18 10:50:17 airties: info",
    "****Merging 2026-04-08-10-49-28_zebra.log **********",
    "****Merging 20260408104928_zebra.log **********",
    "****Merging 20260408_104928_zebra.log **********",
    "2026-04-08 12:44:55.829 [INFO]"
]

for l in lines:
    print(f"Orig: {l}")
    print(f"New : {convert_timestamps_to_cet(l)}")
    print("-")
