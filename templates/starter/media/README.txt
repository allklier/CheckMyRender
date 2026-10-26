Your video goes here.

After the installer this folder already holds a clean demo spot (SB X League ... Slated.mov),
so a first check works right away. In the starter folder (the folder above this one):

    cmr check media/*.mov                              with slate reading (job.yaml)
    cmr check media/*.mov --job job_no_slate.yaml      without slate reading

Spots with planted errors to try: ~/cmr-workspace/demo_spots/errors/
Then remove the demo spot (rm media/*.mov) and copy (or link) your render in:

    cp /path/to/render.mov media/
    ln -s /path/to/render.mov media/        (large files)

Another file type: use its ending, e.g.  cmr check media/*.mxf
cmr only reads the files here; it never changes them. Reports go to ../reports/.
