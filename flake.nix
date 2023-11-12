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
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          inherit pname buildInputs nativeBuildInputs;
        };
        packages.default = pkgs.stdenv.mkDerivation {
          inherit buildInputs nativeBuildInputs pname version src;
        };

      });
}


