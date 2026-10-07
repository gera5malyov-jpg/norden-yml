import re
import runpy
ns = runpy.run_path("tetchair/inspect_site.py", init_globals={"re": re})
print("INSPECT_MATCHES")
for x in ns.get("matches", []):
    print(x)
