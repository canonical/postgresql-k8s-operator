Configuring S3-storage requires that you have a cluster with at least three-nodes deployed and access to S3 storage.  If you don't have a three node replica set read the [Managing Units How-To](/t/charmed-postgresql-tutorial-managing-units/TODO). 

Once you have a three-node cluster deployed, you can configure your settings for S3. Configurations are managed with the [s3-integrator charm](https://charmhub.io/s3-integrator).  Deploy and configure the s3-integrator charm:
```
juju deploy s3-integrator --channel=edge
juju run-action s3-integrator/leader sync-s3-credentials access-key=<access-key-here> secret-key=<secret-key-here> --wait
juju config s3-integrator \
    path="/postgresql-test" \
    region="us-west-2" \
    bucket="postgresql-test-bucket-1" \
    endpoint="https://s3.amazonaws.com"
```
The s3-integrator charm [accepts many configurations](https://charmhub.io/s3-integrator/configure) - enter whatever configurations are necessary for your s3 storage. 

To pass these configurations to Charmed PostgreSQL, relate the two applications:
```
juju relate s3-integrator postgresql-k8s
```

You can also update your configuration options after relating:
```
juju config s3-integrator endpoint=<endpoint>
```