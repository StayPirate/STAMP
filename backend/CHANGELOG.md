# Changelog

## [0.2.0](https://github.com/StayPirate/Sentinel/compare/v0.1.0...v0.2.0) (2026-07-30)


### Features

* add versioning strategy and release-please automation ([882c89a](https://github.com/StayPirate/Sentinel/commit/882c89ac3050273f8a5faf4cd99052a25116c5bb))
* apply testing infrastructure rollout ([c608447](https://github.com/StayPirate/Sentinel/commit/c608447821ea2fa438ff8d48840690727387a26c))
* converge Python version to 3.13 with single source of truth ([77a6aee](https://github.com/StayPirate/Sentinel/commit/77a6aee4355f992faa2d45a4122f8dc83c53edd9))
* type secret configuration fields as SecretStr ([8fd58ce](https://github.com/StayPirate/Sentinel/commit/8fd58cec6432325c0643d009ffb86167646670ed))


### Bug Fixes

* replace python-jose with PyJWT to resolve ecdsa vulnerability ([e230d23](https://github.com/StayPirate/Sentinel/commit/e230d23ef0f8b3c4fdc13e71b846a3a3379e1025))
* resolve test deprecation warning and improve dev setup ([#12](https://github.com/StayPirate/Sentinel/issues/12)) ([fa1cf0c](https://github.com/StayPirate/Sentinel/commit/fa1cf0cfd77926f4eef9256e16a8fb54684afa96))
* target sentinel-smoke project in image compose_exec fixture ([1967942](https://github.com/StayPirate/Sentinel/commit/19679425d58da8fa47d62147b412d1ca1caf09d6))


### Documentation

* add health-endpoints spec, resolve NET-DES-01 ([4b69939](https://github.com/StayPirate/Sentinel/commit/4b69939a677f11c43aa98142b63f2c5d7c33204b))
* apply CVE affected data gaps action plan (OP-1 through OP-6) ([81b86ec](https://github.com/StayPirate/Sentinel/commit/81b86ec03a5a7d17615f9751758da61c06f719f8))
* drop image-testing-setup draft, decouple smoke-test spec ([248532f](https://github.com/StayPirate/Sentinel/commit/248532fb9cc0425431be154729423f8aa162ec3b))
* resolve all 5 configuration review findings ([b817f0b](https://github.com/StayPirate/Sentinel/commit/b817f0b240af35e81ab87c122fb85d78ece14259))
* rewrite architecture.md, move operational content to deployment.md ([52b3f9b](https://github.com/StayPirate/Sentinel/commit/52b3f9baee7b2cecceee9ef635acfc0ee2925b33))
