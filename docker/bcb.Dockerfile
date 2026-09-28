# The sandbox image for BigCodeBench candidates — the NARROW set, `pbt-bcb:1`.
#
# `python:3.12-slim` is the default for APPS, whose tasks are stdio programs over the standard
# library. Every BCB task imports a scientific package at module level, and `_ENTRY_FUNCTION` execs
# the candidate source at harness setup — before any property runs — so a missing import is not one
# failed pair, it is the whole grid returning `ok: False`. The package set below is exactly the
# top-level third-party imports across the 26 tasks in `data/bcb.json`, counted from the candidates
# and the reference solutions.
#
#   docker build -t pbt-bcb:1 - < docker/bcb.Dockerfile
#
# Piped on stdin because there is no COPY here and so no build context is needed; `.` as the context
# would ship 54 MB of apps_hard.json plus .git to the daemon for nothing.
#
# Pinned, because the image decides whether a candidate crashes and an unpinned rebuild months from
# now would silently rescore every suite against different library behaviour.
#
# KEEP THIS FILE. It is the recipe the `bcb` (bcb26) and `bcbas40` runs were scored under, and
# `Run.write_config` refuses a run whose config differs from the one on disk — so re-scoring those
# pools against a different image is not a refresh, it is an error. Wider pools use
# `docker/bcb2.Dockerfile` (`pbt-bcb:2`) instead; see the header there for why.

FROM python:3.12-slim

# Agg has no display to find, which is what a --network none container with no DISPLAY has. Set
# here rather than passed in: `sandbox._docker_command` hands the container `PBT_RESULT` and
# nothing else, so an env var the harness needs has to live in the image.
ENV MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir \
        numpy==2.1.3 \
        pandas==2.2.3 \
        matplotlib==3.9.2 \
        seaborn==0.13.2 \
        scikit-learn==1.5.2 \
        scipy==1.14.1 \
        regex==2024.11.6 \
        pytz==2024.2

# Fail the build rather than the run: an image that cannot import these produces 52 identical
# candidate crashes that read as "the model wrote code that does not run".
RUN python -c "import numpy, pandas, matplotlib, seaborn, sklearn, scipy, regex, pytz; \
import matplotlib.pyplot as plt; plt.figure(); print('bcb sandbox image ok')"
