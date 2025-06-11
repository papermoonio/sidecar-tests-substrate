# Sidecar Tests
Some python scripts to test the sidecar implementation for a substrate chain.

## Contents

* `requirements.txt` has all of the required python packages, which can be installed via pip.  
* `substrate-sidecar-tests.py` has all the tests to monitor the sidecar api

## Run Sidecar 

Please refer to the [Tanssi Documentation](https://docs.tanssi.network/builders/toolkit/substrate-api/libraries/sidecar-api/) or the [official sidecar repository](https://github.com/paritytech/substrate-api-sidecar) for instructions on running a sidecar instance.

## Sidecar tests

Launch tests with Python

```bash
python3 substrate-sidecar-tests.py --substrate-endpoint wss://INSERT_YOUR_ENDPOINT --sidecar-endpoint http://localhost:8080
```
