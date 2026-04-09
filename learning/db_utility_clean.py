import json, math, chromadb

client = chromadb.PersistentClient(path='./ROUTE_PATTERNS_FULL2')
col = client.get_collection('trace_patterns')
total = col.count()
print(f'Total patterns: {total}')

# Fetch all in batches
all_ids = []
all_meta = []
batch = 5000
offset = 0
while offset < total:
    r = col.get(limit=batch, offset=offset, include=['metadatas'])
    all_ids.extend(r['ids'])
    all_meta.extend(r['metadatas'])
    offset += len(r['ids'])

# Find stub violations
to_delete = []
for doc_id, meta in zip(all_ids, all_meta):
    segs = json.loads(meta.get('segments', '[]'))
    if not segs:
        continue
    first = segs[0]
    last = segs[-1]
    first_len = math.hypot(first[2]-first[0], first[3]-first[1])
    last_len = math.hypot(last[2]-last[0], last[3]-last[1])
    if first_len < 0.5 or last_len < 0.5:
        to_delete.append(doc_id)

print(f'Stub violations: {len(to_delete)}')

# Delete in batches
batch_size = 1000
for i in range(0, len(to_delete), batch_size):
    col.delete(ids=to_delete[i:i+batch_size])
    print(f'  Deleted {min(i+batch_size, len(to_delete))} / {len(to_delete)}')

print(f'Remaining: {col.count()}')
