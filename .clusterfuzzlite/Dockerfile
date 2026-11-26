# ClusterFuzzLite build image for the helios-ctl fuzz harnesses (tests/fuzz).
# Atheris and PyInstaller come with the base image; nothing else is installed.
# Pinned by digest; Dependabot (docker, /.clusterfuzzlite) keeps it current.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:6ccbb6b02fcbb8e07d15680e933a6d835cdb579636032cec7d256d0f25d483e4
COPY . $SRC/helios
WORKDIR $SRC/helios
COPY .clusterfuzzlite/build.sh $SRC/
