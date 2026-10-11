# Source-release demonstration

Baseline: 731272ea0a5ed335f53cca35ce8fe0e3ab436823.
Demonstrated source: 4de9674def45f88977087a6f7f5973913f444004.
Target: disposable source archive, Python 3.11.15; no published release or installed service.

At baseline the source-release producer does not exist. The candidate packages
the exact tracked revision, prints its source provenance and passes the actual
checksum command. Appending bytes to that same archive makes the same checksum
fail. Neither successful packaging nor this recording proves release publication.

Run from the repository root, with Python 3.11 on PATH:

    sh docs/demos/source-release/run.sh

[Recorded output](output.log) and [timing](timing.log) are small artifacts retained
in Git. Download both and replay with util-linux:

    scriptreplay --log-out output.log --log-timing timing.log

The output is also readable as a transcript. Raw terminal output retains CRLF.
The final documentation commit preserves the demonstrated producer behavior.
