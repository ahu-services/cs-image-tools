# Changelog

All notable changes to this project are documented here. Earlier releases are
described in the [GitHub releases](https://github.com/ahu-services/cs-image-tools/releases).

## [1.12.0] - unreleased

Without new variables the image runs as before; auto-configuration stays off
by default and all tuning is optional.

### Updated

- FFmpeg 9.0.2 -> 9.1, Debian base image 13.7 -> 13.8. ImageMagick
  7.1.2-36, Ghostscript 10.08.0, ExifTool 13.64, pngquant 3.0.3 and the
  Python packages are already the latest releases.

### Security

- Published images never contain censhare software or credentials. For private
  builds with a pre-installed Service-Client, repository credentials are
  BuildKit secrets (`--secret id=repo_user` / `id=repo_pass`) instead of build
  args, so they stay out of the build cache, intermediate stages and build
  provenance. `--build-arg REPO_USER/REPO_PASS` no longer has any effect; the
  build fails if only one secret is given or secrets are given without
  `VERSION`.
- New `.dockerignore` keeps `.git`, `.env` and other local files out of the
  build context.
- The runtime download sends the repository credentials as HTTP basic auth
  instead of embedding them in the URL (special characters in passwords work).
- `REPO_USER`, `REPO_PASS`, `SVC_PASS` and, once `hosts.xml` is written,
  `VOLUMES_INFO` are removed from the environment before the Service-Client
  starts, so the JVM and the media tools do not inherit them.

### Changed

- ImageMagick `max-memory-request` raised from 1GiB to 1536MiB. It limits the
  largest single allocation, not a cache budget. Layered Photoshop TIFF masters
  keep their layers in TIFF tag 37724 (ImageSourceData), which ImageMagick reads
  as one profile block that cannot spill to disk; blocks of 330-980 MB were
  seen on 0.4-1.4 GB masters. A block above the limit fails every conversion
  with `Memory allocation failed`.
- Auto-configuration no longer derives `max-memory-request` from the
  per-worker cache budget (it set 256MiB on 8 GB / 4 instances, which broke
  these files) and never lowers the bundled value.
- ImageMagick `width`/`height` raised from 20KP to 32KP. `area` stays 400MP:
  larger images are cached to disk, not rejected.
- Auto-configuration reserves memory for half of the instances reading a large
  layered TIFF at the same time, derives `thread` from the CPU quota and `disk`
  from the free temp space divided by `SVC_INSTANCES`. The memory limit comes
  from the cgroup (v2/v1), then `CONTAINER_MEMORY_LIMIT`, then the ECS task
  metadata; without one it logs a warning and changes nothing.
- The policy is rendered from a pristine copy on every start, so removed
  overrides do not survive a container restart.

### Added

- `IMAGEMAGICK_POLICY_<NAME>` overrides for all resource settings (`MEMORY`,
  `MAP`, `DISK`, `AREA`, `WIDTH`, `HEIGHT`, `TIME`, `THREAD`, `LIST_LENGTH`,
  `FILE`, `THROTTLE`, `TEMPORARY_PATH`, `MAX_MEMORY_REQUEST`) with validation;
  invalid values and unknown names are ignored with a warning.
- `CONTAINER_MEMORY_LIMIT` as a generic memory limit where the cgroup shows
  none.
- Start log: every effective policy value with its source (bundled,
  autoconfig, env), the detected memory limit and its source, `SVC_INSTANCES`,
  the ImageMagick facility timeout and the free temp space, plus a warning
  when the temp space is below `disk` x `SVC_INSTANCES`. Never blocks or fails
  the start.
- After the start, a background thread logs a growing container memory peak
  and every rise of the OOM kill counter (cgroup v2 `memory.events`, v1
  `memory.oom_control` or `memory.failcnt`).
- Image tests with synthetic layered TIFFs (750 MB layer block): the
  conversion works with the auto-configured policy for 8 GB / 4 instances and
  fails with `max-memory-request=256MiB`.

### Notes

- Measured with a synthetic layered TIFF (0.86 GiB block): reading it needs
  about 2.6 GiB of anonymous memory (about 3x the block) plus the
  memory-mapped input file, independent of the pixel-cache limits. `[0]`,
  `-define tiff:ignore-layers=true` and `-define tiff:ignore-tags=37724` do not
  avoid loading the block; they only skip decoding the layers.
- ImageMagick `time` stays 120 s, below the 300 s facility timeout, because
  callers may give up earlier and leave the process running.
