# Repository identity follows the release context

Run `python3 docs/demos/repository-identity/reproduce.py` from the checkout.
The reproduction imports the actual verifier at baseline `7162eaa0` and the
candidate, then captures their download arguments with a synthetic subprocess.
Both receive `GITHUB_REPOSITORY=intigraph/camayoc`; the baseline still targets
`scbrown/camayoc`, while the candidate targets `intigraph/camayoc`.
Both refuse the absent synthetic asset. No network, credentials, upload, or
production store is involved.

Replay the recorded terminal using `scriptreplay --log-timing timing.log
--log-out output.log`. This demonstrates target selection only. The real
v0.1.5 release's missing pendant assets require separate producer acceptance;
this source change does not repair existing release assets.
