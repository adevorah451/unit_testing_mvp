# The sandbox image for BigCodeBench candidates — the WIDE set, `pbt-bcb:2`.
#
#   docker build -t pbt-bcb:2 - < docker/bcb2.Dockerfile
#
# `pbt-bcb:1` (docker/bcb.Dockerfile) carries exactly the imports of the 26 tasks in
# `data/bcb.json`. The AttackSelection pools draw from all 1140 BigCodeBench problems, so their
# import surface is a superset: 35 distinct third-party modules across the 453 tasks in
# `data/bcbas.json`, against that image's 8. A module the image lacks is not one failed pair — the
# harness execs candidate source at setup, so it is the whole grid, recorded as `infra`. On the
# 40-problem pilot this cost 3 tasks outright (flask, flask_restful), and the same scan says it
# would cost roughly a tenth of the full pool.
#
# The original eight keep pbt-bcb:1's EXACT pins, and are installed in the same pip call as
# everything else so the resolver cannot lift numpy out from under scikit-image or statsmodels. A
# suite's verdict depends on library behaviour, so a pool scored under :1 and a pool scored under :2
# must agree about numpy, pandas, matplotlib, seaborn, sklearn, scipy, regex and pytz or the two
# are not comparable. The added pins are the versions resolved on 2026-09-25 and frozen.
#
# WHAT THIS IMAGE STILL CANNOT FIX: the sandbox runs `--network none`, and about 11 of the 453
# tasks call out at runtime (`requests.get`, `urlretrieve`, `urlopen`, `mechanize.Browser`). Those
# fail on the network, not on an import, and no package set changes that.

FROM python:3.12-slim

# Agg has no display to find, which is what a --network none container with no DISPLAY has. Set
# here rather than passed in: `sandbox._docker_command` hands the container `PBT_RESULT` and
# nothing else, so an env var the harness needs has to live in the image.
ENV MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1

# opencv and lxml load shared objects that the slim base does not ship, even from wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        numpy==2.1.3 \
        pandas==2.2.3 \
        matplotlib==3.9.2 \
        seaborn==0.13.2 \
        scikit-learn==1.5.2 \
        scipy==1.14.1 \
        regex==2024.11.6 \
        pytz==2024.2 \
        requests==2.34.2 \
        python-dateutil==2.9.0.post0 \
        nltk==3.10.3 \
        opencv-python-headless==5.0.0.93 \
        flask==3.1.3 \
        flask-restful==0.3.10 \
        flask-mail==0.10.0 \
        statsmodels==0.15.0 \
        pillow==12.3.0 \
        beautifulsoup4==4.15.0 \
        psutil==7.2.2 \
        xlwt==1.3.0 \
        rsa==4.9.1 \
        wordcloud==1.9.6 \
        holidays==0.105 \
        scikit-image==0.26.0 \
        mechanize==0.4.10 \
        texttable==1.7.0 \
        natsort==8.4.0 \
        openpyxl==3.1.5 \
        prettytable==3.18.0 \
        cryptography==50.0.1 \
        pyyaml==6.0.3 \
        sympy==1.14.0 \
        faker==40.39.0 \
        lxml==6.1.3

# Baked in, not downloaded: one task calls `word_tokenize`, and a corpus fetched at runtime inside
# a --network none container is a crash rather than a download.
RUN python -m nltk.downloader -d /usr/share/nltk_data punkt punkt_tab stopwords wordnet
ENV NLTK_DATA=/usr/share/nltk_data

# Fail the build rather than the run. Every module below is one the 453-task pool imports at module
# level, so an image that cannot import one of them produces a run of identical `infra` failures
# that read as the sandbox being broken rather than the image being short.
RUN python -c "\
import importlib; \
mods = 'numpy pandas matplotlib seaborn sklearn scipy regex pytz mpl_toolkits requests dateutil \
nltk cv2 flask statsmodels PIL bs4 psutil xlwt rsa wordcloud flask_restful holidays skimage \
mechanize texttable natsort flask_mail openpyxl prettytable cryptography yaml sympy faker \
lxml'.split(); \
[importlib.import_module(m) for m in mods]; \
import matplotlib.pyplot as plt; plt.figure(); \
from nltk.tokenize import word_tokenize; assert word_tokenize('a b c'); \
print('bcb wide sandbox image ok —', len(mods), 'modules')"
