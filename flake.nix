{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
      };
      pname = "cv";
      version = "0.1.1";
      src = ./.;
    in
    {
      packages.${system}.default = pkgs.mkYarnPackage {
        inherit pname version src;
        nativeBuildInputs = [ pkgs.bun ];
        offlineCache = pkgs.fetchYarnDeps {
          yarnLock = "${src}/yarn.lock";
          hash = "sha256-HKoIv3YMKy5uZ/wT2grg+Z6k4CMykY9dj/okfA2uias=";
        };
        buildPhase = ''
          yarn --offline build
        '';
        doDist = false;
        postInstall = ''
          ls -alh
          rm -rf $out/lib
          rm -rf $out/libexec
          rm -rf $out/bin
        '';
      };
    };
}
