{ pkgs, package }:

pkgs.dockerTools.buildLayeredImage {
  name = "ghcr.io/genebean/ytdlfin";
  tag = "latest";

  contents = [
    package
    # CA certificates for outbound HTTPS to the OIDC provider.
    pkgs.cacert
    # Minimal /etc/passwd and /etc/group so getpwuid(3) resolves when the
    # container runs as a non-root UID via --userns=keep-id.
    pkgs.dockerTools.fakeNss
  ];

  config = {
    Cmd = [ "${package}/bin/ytdlfin" ];

    Env = [
      "DATA_DIR=/data"
      "STAGING_DIR=/staging"
      "PORT=8001"
      # Standard locations for CA bundles used by Python/authlib.
      "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
      "NIX_SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
    ];

    ExposedPorts = {
      "8001/tcp" = { };
    };

    # Declare the two directories that must be mounted at runtime.
    Volumes = {
      "/data" = { };
      "/staging" = { };
    };
  };
}
