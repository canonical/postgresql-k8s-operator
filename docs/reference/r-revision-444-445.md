>Reference > Release Notes > [All revisions] > Revision 444/445

# Revision 444/445 (hotfix for 381/382)
<sub>12 November 2024</sub>

Dear community,

Canonical has released a hotfix for Charmed PostgreSQL K8s operator in the [14/stable channel]:
* Revision 444 is built for `amd64` on Ubuntu 22.04 LTS (postgresql-image r162)
* Revision 445 is built for `arm64` on Ubuntu 22.04 LTS (postgresql-image r162)

## Highlights 

This is a hotfix release to add Juju 3.6 compatibility for the previous stable [revisions 381/382](/t/15442). 

## Bugfixes and stability

* Fixed Juju 3.6 support - fixed Pebble 1.12+ compatibility ([DPE-5915](https://warthogs.atlassian.net/browse/DPE-5915))

## Known limitations

See the [Release Notes for Revisions 381/382](/t/15442).

If you are jumping over several stable revisions, check [previous release notes][All revisions] before upgrading.

<!-- LABELS-->
[All revisions]: /t/11872
[system requirements]: /t/11744

[14/stable channel]: https://charmhub.io/postgresql?channel=14/stable