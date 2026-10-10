p = "tests/test_admin_ops.py"
s = open(p, encoding="utf-8").read()
old = '    set_jobs(db, business, finished_at=datetime.now(UTC) - timedelta(days=2))\n'
assert old in s
new = '''    for event in api.get(f"{A}/events", headers=staff).json()["items"]:  # the failure is known about
        api.post(f"{A}/events/{event['id']}/resolve", headers=staff)
''' + old
s = s.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(s)

p = "tests/test_admin.py"
s = open(p, encoding="utf-8").read()
i = s.index('"last_activity_at"}')
s = s[:i] + '"last_activity_at", "open_cases", "notes"}' + s[i + len('"last_activity_at"}'):]
open(p, "w", encoding="utf-8").write(s)
