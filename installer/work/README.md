My CMR files (separate from the demo workspace ../demo, which the installer keeps current):
  specs/     spec sheets (one per kind of deliverable)
  profiles/  rules (tolerances, severities)
  jobs/      one job file per project: cmr check FILE --job jobs/<project>.yaml
  reports/   JSON reports (job_template.yaml writes here: output: ../reports)
Copy templates from ../demo/templates; do not edit them there.
The installer set this folder up once and never changes or removes it (also not on upgrade or
uninstall). Paths in the job files are relative, so the whole cmr folder can be moved.
