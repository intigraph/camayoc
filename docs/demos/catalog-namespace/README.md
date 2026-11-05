# Delivery catalog namespace

This recording runs the same native catalog renderer in disposable stores on
base `05f92e2d780bf4f0bf0291851e98a93ca462eda0` and the candidate. The base
refuses a minting write with no usable base namespace. The candidate renders the
catalog and checks that its Ansible row and existing README match exactly.
The native engine is clean Quipu 0.17.0 (`647c2dd2`). No live store is used.

Download [output.log](output.log) and [timing.log](timing.log), then replay locally:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

This is a terminal recording; inline playback is not provided. Reproduce with:

```sh
just review-test -q tests/test_delivery_catalog.py
```

The small recording files are retained with source for review. The renderer's
private configuration uses its existing namespace; it does not migrate a store
or change the namespace policy of older Quipu releases.
