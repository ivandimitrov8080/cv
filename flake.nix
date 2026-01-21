{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    configuration.url = "github:ivandimitrov8080/configuration.nix";
    systems.url = "github:nix-systems/default";
    # nvim config helper
    nixvim-flake.url = "github:nix-community/nixvim";
    nixvim-flake.inputs.nixpkgs.follows = "nixpkgs";
    # neovim latest version
    neovim-nightly-overlay.url = "github:nix-community/neovim-nightly-overlay";
    neovim-nightly-overlay.inputs.nixpkgs.follows = "nixpkgs";
    devenv.url = "github:cachix/devenv";
    devenv.inputs.nixpkgs.follows = "nixpkgs";
    treefmt-nix.url = "github:numtide/treefmt-nix";
  };
  outputs =
    inputs@{
      nixpkgs,
      configuration,
      systems,
      nixvim-flake,
      neovim-nightly-overlay,
      devenv,
      treefmt-nix,
      ...
    }:
    let
      eachSystem = nixpkgs.lib.genAttrs (import systems);
    in
    {
      packages = eachSystem (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };
        in
        {
          default = pkgs.stdenv.mkDerivation {
            name = "cv";
            version = "1.0";
            src = ./.;
            buildInputs = with pkgs; [
              (ghc.withPackages (
                hp: with hp; [
                  HPDF
                  aeson
                ]
              ))
            ];
            buildPhase = ''
              runHook preBuild
              runghc cv.hs
              runHook postBuild
            '';
            installPhase = ''
              runHook preBuild
              mkdir -p $out
              cp cv.pdf $out
              runHook postBuild
            '';
          };
        }
      );
      devShells = eachSystem (
        system:
        let
          nixvim-default = nixvim-flake.legacyPackages.${system}.makeNixvim {
            package = neovim-nightly-overlay.packages.${system}.default;
          };
          pkgs = import nixpkgs {
            inherit system;
            overlays = [
              (_final: _prev: {
                nixvim = nixvim-default;
              })
              configuration.overlays.default
            ];
          };
        in
        {
          default = devenv.lib.mkShell {
            inherit inputs pkgs;
            modules = [
              {
                packages = with pkgs; [
                  (nixvim.haskell.extend {
                    lsp.servers.jsonls.enable = true;
                  })
                  (ghc.withPackages (
                    hp: with hp; [
                      HPDF
                      aeson
                    ]
                  ))
                ];
                git-hooks.hooks = {
                  nixfmt.enable = true;
                  prettier.enable = true;
                  deadnix.enable = true;
                  statix.enable = true;
                  ormolu.enable = true;
                  ormolu.settings.defaultExtensions = [
                    "ImportQualifiedPost"
                  ];
                };
              }
            ];
          };
        }
      );
      formatter = eachSystem (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        (treefmt-nix.lib.evalModule pkgs {
          projectRootFile = "flake.nix";
          programs = {
            nixfmt.enable = true;
            prettier.enable = true;
            deadnix.enable = true;
            statix.enable = true;
            ormolu.enable = true;
            ormolu.ghcOpts = [
              "ImportQualifiedPost"
            ];
          };
        }).config.build.wrapper
      );
      checks = eachSystem (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
            overlays = [ configuration.overlays.default ];
          };
        in
        {
          default = pkgs.testers.runNixOSTest {
            name = "test";
            nodes = {
              machine =
                { pkgs, ... }:
                {
                  environment.systemPackages = [ pkgs.hello ];
                };
            };
            testScript =
              #py
              ''
                machine.wait_for_unit("multi-user.target");
                machine.succeed("hello");
              '';
          };
        }
      );
    };
}
