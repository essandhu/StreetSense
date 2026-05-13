# config/

Runtime configuration for StreetSense. Anything that varies between
deployments lives here, not in code.

## Cities

`config/cities/` holds one YAML file per supported city. The shape is
defined by `config/cities/__schema__.yaml` (JSON Schema in YAML). Adding a
new city is a config change — never a code change (extension point #2).

```bash
make seed CITY=cambridge       # default if CITY is unset
```

Exactly one city file should set `default: true`.
