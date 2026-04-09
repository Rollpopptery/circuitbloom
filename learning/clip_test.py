
import chromadb
client = chromadb.PersistentClient(path='trace_pattern_collection')
c = client.get_collection('trace_patterns')
r = c.get(limit=100, include=['metadatas'])
layers = set()
for m in r['metadatas']:
    layers.add(m.get('layers', '?'))
print('Layer indices in DB:', sorted(layers))
