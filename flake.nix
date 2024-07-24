{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    ide = {
      url = "github:ivandimitrov8080/flake-ide";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, ide, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system; overlays = [
        (final: prev: {
          nvim = ide.nvim.${system}.standalone.default {
            plugins = {
              lsp.servers = {
                tsserver.enable = true;
                jsonls.enable = true;
                tailwindcss.enable = true;
              };
            };
          };
        })
      ];
      };
      pname = "cv";
      version = "0.1.1";
      src = ./.;
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        inherit pname;
        buildInputs = with pkgs; [
          nvim
          bun
        ];
      };
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
