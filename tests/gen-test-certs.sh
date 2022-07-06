#!/bin/bash
mkdir -p tests/tls
openssl req -new -nodes -text -out tests/tls/root.csr \
  -keyout tests/tls/root.key -subj "/CN=root.example.com"
chmod og-rwx tests/tls/root.key
openssl x509 -req -in tests/tls/root.csr -text -days 3650 \
  -extfile /etc/ssl/openssl.cnf -extensions v3_ca \
  -signkey tests/tls/root.key -out tests/tls/root.crt
openssl req -new -nodes -text -out tests/tls/server.csr \
  -keyout tests/tls/server.key -subj "/CN=dbhost.example.com"
chmod og-rwx tests/tls/server.key
openssl x509 -req -in tests/tls/server.csr -text -days 365 \
  -CA tests/tls/root.crt -CAkey tests/tls/root.key -CAcreateserial \
  -out tests/tls/server.crt