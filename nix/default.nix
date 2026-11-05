{ pkgs, ... }: {
  chromium-sandboxed =
    pkgs.writers.writeNuBin "chromium-sandboxed"
      {
        makeWrapperArgs = [
          "--prefix"
          "PATH"
          ":"
          "${pkgs.lib.makeBinPath [
            pkgs.chromium
            pkgs.bubblewrap
          ]}"
        ];
      }
      ''
        def --wrapped main [...rest] {
          let xrd = ($env.XDG_RUNTIME_DIR? | default "/run/user/1000")
          let wl = ($env.WAYLAND_DISPLAY? | default "wayland-1")
          let sock = ($xrd | path join $wl)
          let opts = ["--die-with-parent" "--unshare-all" "--share-net" "--new-session" "--proc" "/proc" "--dev" "/dev" "--dev-bind" "/dev/dri" "/dev/dri" "--tmpfs" "/dev/shm" "--tmpfs" "/tmp" "--ro-bind" "/nix/store" "/nix/store" "--ro-bind-try" "/etc/fonts" "/etc/fonts" "--ro-bind-try" "/etc/ssl" "/etc/ssl" "--ro-bind-try" "/etc/resolv.conf" "/etc/resolv.conf" "--ro-bind-try" "/etc/nsswitch.conf" "/etc/nsswitch.conf" "--ro-bind-try" "/run/opengl-driver" "/run/opengl-driver" "--dir" "/home" "--dir" "/home/ivand" "--dir" "/home/ivand/src" "--dir" "/home/ivand/src/cv" "--ro-bind" "/home/ivand/src/cv/result" "/home/ivand/src/cv/result" "--dir" "/run/user" "--tmpfs" $xrd "--bind" $sock $sock "--chdir" "/tmp" "--setenv" "HOME" "/tmp" "--setenv" "XDG_RUNTIME_DIR" $xrd "--setenv" "WAYLAND_DISPLAY" $wl]
          exec bwrap ...$opts -- chromium --remote-debugging-port=9222 ...$rest
        }
      '';
}
