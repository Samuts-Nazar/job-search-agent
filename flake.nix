{
  description = "Job search agent - dev shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python312
            uv
            ruff
            typst
            nodejs_22
            dejavu_fonts
          ];

          # Manylinux wheels (numpy, pandas, ...) pulled in by uv expect these
          # shared libs on a normal linker search path, which NixOS doesn't have.
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];

          shellHook = ''
            export UV_PYTHON=${pkgs.python312}/bin/python3
            # DejaVu Sans covers Latin + Cyrillic (PROJECT.md §8) -- pin it
            # explicitly rather than relying on whatever fonts the host
            # happens to have installed system-wide.
            export TYPST_FONT_PATHS="${pkgs.dejavu_fonts}/share/fonts/truetype"
          '';
        };
      });
}
