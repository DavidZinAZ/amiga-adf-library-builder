# Gotek Export Format — Phase 1

> **Reference status:** Current stable Gotek export-format reference. Historical upstream research is archived under `archive/research/`.

Use one game/release folder directly beneath `ADF` or `DSK`.

Example:

```text
/ADF/Oil Imperium [v1.1e] [QTX]/
  Oil Imperium [v1.1e] [QTX]-1.adf
  Oil Imperium [v1.1e] [QTX]-2.adf
  Oil Imperium [v1.1e] [QTX].jpg
  Oil Imperium [v1.1e] [QTX].nfo
```

Do not assume nested version or release-group directories are supported in Phase 1.


Disk-specific media can override the shared release companions. For example,
`Oil Imperium [v1.1e] [QTX]-2.jpg` and `Oil Imperium [v1.1e] [QTX]-2.rtfm`
apply to disk 2. Disks without an override inherit the shared `.jpg`/`.rtfm`.
Changing back to shared mode removes obsolete managed disk companions when
exporting again to the same staging run. Original sources remain untouched.

Verified against upstream `findJPGFor` and `manualFor`: exact disk stem first,
then game base/folder fallback. The game-list cover itself is cached at release
level by upstream `drawCoverPanel`; exporting unique companions does not change
that firmware behaviour. [Upstream firmware](https://github.com/mesarim/Gotek-Touchscreen-interface/blob/main/firmware/Gotek_JC3248/Gotek_JC3248.ino).
