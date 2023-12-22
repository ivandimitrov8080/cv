{
  description = "CV template";

  inputs = {
    nixpkgs.url = "nixpkgs";
    systems.url = "github:nix-systems/x86_64-linux";
    flake-utils = {
      url = "github:numtide/flake-utils";
      inputs.systems.follows = "systems";
    };
    ide = {
      url = "github:ivandimitrov8080/flake-ide";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, systems, ide }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      pname = "cv";
      version = "0.1.0";
      src = ./.;
      nvim = ide.nvim.${system}.standalone {
        plugins = {
          lsp.servers = {
            tsserver.enable = true;
            jsonls.enable = true;
            tailwindcss.enable = true;
          };
        };
      };
      buildInputs = with pkgs; [
        nvim
        inotify-tools
      ];
      nativeBuildInputs = with pkgs; [
        bun
      ];
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        inherit pname buildInputs nativeBuildInputs;
        shellHook = ''
          echo "$$" > ./pid
          monitor() {
            while true; do
              inotifywait -e modify ${pname}.tsx > /dev/null 2>&1
              make
              pkill -HUP mupdf
            done
          }
          monitor > /dev/null 2>&1 &
        '';
      };
      packages.${system}.default = pkgs.buildNpmPackage rec {
        inherit nativeBuildInputs pname version src;
        npmDepsHash = "sha256-1fqP3HFhziup3U97t+Kx8JMAd+ZpJmz5WZATR1CaQZY=";
        postInstall = ''
          ls -alh
          mkdir -p $out
          rm -rf $out/lib
        '';
      };
    };
}
