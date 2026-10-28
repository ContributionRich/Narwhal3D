# Application logo

`narwhal.png` and `narwhal_vector.svg` are the matching logo sources supplied by the project maintainer. Keep them as the canonical artwork; do not redraw the logo procedurally.

Regenerate the application/menu/desktop PNG, multi-size Windows ICO, and native scalable icon with:

```sh
/usr/bin/python3 launch/make_icon.py
/usr/bin/python3 launch/make_icon.py --check
```

Generation uses Pillow as a build-time image-processing dependency. The sources are not modified. The welcome executable embeds `launch/narwhal3d.png`; native runtime and Windows executable resources use the generated assets when rebuilt. Existing published packages are immutable and are not updated by regenerating source assets.
