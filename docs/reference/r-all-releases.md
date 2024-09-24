# Release Notes

Here you will find release notes for major revisions of this charm that are available in the [Charmhub `stable` channel](https://juju.is/docs/juju/channel#heading--risk).

To see  **all** charm revisions, check the [Charmed PostgreSQL K8s Releases page](https://github.com/canonical/postgresql-k8s-operator/releases) on GitHub.

## At a glance

The table below is a high-level overview of the architectures and integrations that are supported by each charm revision. 

| Revision | PostgreSQL | amd64 | arm64 | [TLS  encryption](/t/9685)* | [Monitoring (COS, Grafana)](/t/10600)  | [Tracing (Tempo K8s)](/t/14521)  |
|:--------:|:-----:|:-----:|:-----:|:--------------------:|:---------------:|:--------------------:|
|      [382](/t/15442) | 14.12 | :heavy_multiplication_x:   |    :white_check_mark:   |          :white_check_mark:            |        :white_check_mark:         |         :white_check_mark:           |
|     [381](/t/15442)  | 14.12 |    :white_check_mark:    | :heavy_multiplication_x: |          :white_check_mark:           |        :white_check_mark:         |         :white_check_mark:           |
|     [281](/t/14068)  | 14.11 |    :white_check_mark:    | :heavy_multiplication_x: |          :white_check_mark:           |        :white_check_mark:         |         :white_check_mark:           |
|      [280](/t/14068) | 14.11 |  :heavy_multiplication_x:   |    :white_check_mark:   |          :white_check_mark:            |        :white_check_mark:         |         :white_check_mark:           |
|      [193](/t/13208) | 14.10 |   :white_check_mark:   |    :heavy_multiplication_x:   |          :white_check_mark:            |        :white_check_mark:        |           :heavy_multiplication_x:         |
|      [177](/t/12668) |  14.9 |  :white_check_mark:    |    :heavy_multiplication_x:   |            :heavy_multiplication_x:          |        :white_check_mark:         |           :heavy_multiplication_x:         |
|      [158](/t/11874) |  14.9 |  :white_check_mark:    |    :heavy_multiplication_x:   |            :heavy_multiplication_x:          |       :white_check_mark:          |           :heavy_multiplication_x:        |
|      [73](/t/11873) | 14.7 |   :white_check_mark:    |    :heavy_multiplication_x:   |           :heavy_multiplication_x:          |       :heavy_multiplication_x:     |       :heavy_multiplication_x:     |


**TLS encryption***: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

For more details about a particular revision, refer to its dedicated Release Notes page.
For more details about each feature/interface, refer to their dedicated How-To guide.

### Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/t/10945).