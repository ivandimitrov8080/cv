{
  description = "CV template";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs";
    systems.url = "github:nix-systems/x86_64-linux";
    flake-utils = {
      url = "github:numtide/flake-utils";
      inputs.systems.follows = "systems";
    };
  };

  outputs = { self, nixpkgs, flake-utils, systems }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        pname = "cv";
        version = "0.0.1";
        src = ./.;
        buildInputs = with pkgs; [
        ];
        nativeBuildInputs = with pkgs; [
          nodejs_20
          bun
          inotify-tools
        ];
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
      in
      {
        devShells.default = pkgs.mkShell {
          inherit pname buildInputs nativeBuildInputs shellHook;
        };
        packages.default = pkgs.stdenv.mkDerivation {
          inherit buildInputs nativeBuildInputs pname version src;
        };

      });
}


