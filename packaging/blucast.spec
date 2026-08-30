Name:           blucast
Version:        1.2.0
Release:        1%{?dist}
Summary:        Real-time AI-powered video effects using NVIDIA Maxine VideoFX SDK

License:        MIT
URL:            https://github.com/MAlexVR/Blucast
Source0:        https://github.com/MAlexVR/Blucast/archive/refs/tags/v%{version}/blucast-%{version}.tar.gz

BuildArch:      noarch

Requires:       podman
Requires:       v4l2loopback-kmod
Requires:       hicolor-icon-theme
Requires:       xdg-utils

%description
BluCast adds AI-powered video effects — background removal/replacement/
blur, face-tracking Auto Reframe, a Virtual Light relighting effect, and
more — to any webcam on Linux, exposing the result as a virtual camera
device that any video call app can select. It's built on the NVIDIA
Maxine VideoFX SDK, the same technology behind NVIDIA Broadcast.

The actual video pipeline runs inside a container image, pulled
automatically the first time you launch BluCast; this package only
installs the host-side launcher, desktop entry, icon, and the
v4l2loopback virtual-camera kernel module configuration.

Before using BluCast, make sure you separately have:
 * An NVIDIA GPU with a working driver (verify with %{_bindir}/nvidia-smi)
 * The NVIDIA Container Toolkit configured for GPU passthrough — see
   https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
 * A generated NVIDIA CDI spec: sudo nvidia-ctk cdi generate

%prep
%autosetup -n Blucast-%{version}

%build
# Nothing to compile here. The C++ video server and its NVIDIA/OpenCV
# dependencies are built into the container image (pulled from
# ghcr.io/andrei9383/blucast on first launch), never on the RPM-installing
# host — see README.md's "Distribution / Packaging" section for why.

%install
install -Dm755 run.sh                 %{buildroot}%{_datadir}/blucast/run.sh
install -Dm755 scripts/vcam_watcher.sh %{buildroot}%{_datadir}/blucast/vcam_watcher.sh
install -Dm644 app/control_panel.py   %{buildroot}%{_datadir}/blucast/control_panel.py
install -Dm644 assets/logo.svg        %{buildroot}%{_datadir}/blucast/logo.svg

install -Dm755 packaging/blucast-wrapper %{buildroot}%{_bindir}/blucast

install -Dm644 packaging/blucast-v4l2loopback-load.conf \
    %{buildroot}%{_prefix}/lib/modules-load.d/blucast-v4l2loopback.conf
install -Dm644 packaging/blucast-v4l2loopback.conf \
    %{buildroot}%{_prefix}/lib/modprobe.d/blucast-v4l2loopback.conf
install -Dm644 packaging/83-blucast-vcam.rules \
    %{buildroot}%{_prefix}/lib/udev/rules.d/83-blucast-vcam.rules

install -Dm644 assets/logo.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/blucast.svg

install -d -m755 %{buildroot}%{_datadir}/applications
cat > %{buildroot}%{_datadir}/applications/blucast.desktop <<EOF
[Desktop Entry]
Name=BluCast
Comment=AI-Powered Virtual Camera
Exec=%{_bindir}/blucast
Icon=blucast
Terminal=false
Type=Application
Categories=Video;AudioVideo;
StartupWMClass=blucast
EOF

%post
udevadm control --reload-rules >/dev/null 2>&1 || :
cat <<'EOF'
BluCast installed. Before first launch, make sure you have:
  - An NVIDIA driver installed (check with: nvidia-smi)
  - The NVIDIA Container Toolkit configured for GPU passthrough
  - A generated NVIDIA CDI spec: sudo nvidia-ctk cdi generate

The v4l2loopback virtual camera module will load automatically on next
boot. To use it immediately without rebooting:
  sudo modprobe v4l2loopback devices=1 video_nr=10 \
      card_label="BluCast Virtual Camera" exclusive_caps=1
EOF

%postun
if [ "$1" -eq 0 ]; then
    udevadm control --reload-rules >/dev/null 2>&1 || :
fi

%files
%license LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/blucast
%{_datadir}/blucast/
%{_datadir}/applications/blucast.desktop
%{_datadir}/icons/hicolor/scalable/apps/blucast.svg
%{_prefix}/lib/modules-load.d/blucast-v4l2loopback.conf
%{_prefix}/lib/modprobe.d/blucast-v4l2loopback.conf
%{_prefix}/lib/udev/rules.d/83-blucast-vcam.rules

%changelog
* Sun Aug 30 2026 Mauricio Vargas <mvargas.rodriguez@gmail.com> - 1.2.0-1
- Initial RPM packaging

* Sun Aug 30 2026 Mauricio Vargas <mvargas.rodriguez@gmail.com> - 1.1.1-1
- Installer consistency fixes (see CHANGELOG.md)

* Sun Aug 30 2026 Mauricio Vargas <mvargas.rodriguez@gmail.com> - 1.1.0-1
- Auto Reframe, Virtual Light, multi-language UI, build no longer needs a
  manual NVIDIA SDK download (see CHANGELOG.md)
