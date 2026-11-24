import pytest

np = pytest.importorskip("numpy")

from core import composite  # noqa: E402

SIZE = 16


def flat(*channels):
    return np.broadcast_to(np.asarray(channels, dtype=np.float32), (4, 4, len(channels))).copy()


def inputs(**overrides):
    """A weapon that is fully paintable, with nothing worn and no grime."""
    textures = {
        "g_tColor": flat(0.2, 0.4, 0.6),
        "g_tMetalness": flat(0.9, 0.8),
        "g_tAmbientOcclusion": flat(1.0, 1.0, 1.0, 0.0),
        "g_tMasks": flat(1.0, 0.0, 0.0),
        "g_tWear": flat(0.0),
        "g_tGrunge": flat(1.0, 1.0, 1.0, 1.0),
        "g_tPattern": flat(0.8, 0.5, 0.1, 1.0),
        "g_tPosition": flat(0.5, 0.5, 0.5),
        "g_tSurface": flat(0.5, 0.5, 1.0),
    }
    textures.update(overrides)
    return textures


def test_layer_placement():
    identity = composite.uv_transform(0.0, 1.0, (0.0, 0.0))
    assert np.allclose(identity, ((1, 0, 0), (0, 1, 0)), atol=1e-6)
    doubled = composite.uv_transform(0.0, 2.0, (0.25, 0.0))
    assert np.allclose(doubled, ((2, 0, 0.25), (0, 2, 0)), atol=1e-6)
    # A quarter turn keeps the middle of a repeating layer in the middle of a tile.
    turned = composite.uv_transform(90.0, 1.0, (0.0, 0.0))
    u = turned[0][0] * 0.5 + turned[0][1] * 0.5 + turned[0][2]
    v = turned[1][0] * 0.5 + turned[1][1] * 0.5 + turned[1][2]
    assert np.allclose((u % 1.0, v % 1.0), (0.5, 0.5), atol=1e-6)
    assert np.allclose(turned[0][:2], (0, -1), atol=1e-6) and np.allclose(turned[1][:2], (1, 0), atol=1e-6)


def test_sampling_wraps_or_clamps():
    image = np.array([[[0.0], [1.0]], [[2.0], [3.0]]], dtype=np.float32)
    centre = composite.sample(image, np.array([0.25]), np.array([0.25]))
    assert centre[0, 0] == 0.0
    between = composite.sample(image, np.array([0.5]), np.array([0.25]))
    assert between[0, 0] == pytest.approx(0.5)
    clamped = composite.sample(image, np.array([1.2]), np.array([0.25]), composite.CLAMP, composite.CLAMP)
    wrapped = composite.sample(image, np.array([1.25]), np.array([0.25]), composite.WRAP, composite.WRAP)
    assert clamped[0, 0] == 1.0 and wrapped[0, 0] == 0.0


def test_fit_keeps_flat_images_flat():
    big = flat(0.3, 0.6, 0.9)
    big = np.tile(big, (8, 8, 1))
    assert np.allclose(composite.fit(big, 8), 0.3 * np.array([1, 2, 3]), atol=1e-6)
    assert composite.fit(big, 8).shape == (8, 8, 3)


def test_falling_edge():
    assert composite.smoothstep(1.0, 0.9, 1.0) == 0.0
    assert composite.smoothstep(1.0, 0.9, 0.8) == 1.0
    assert composite.smoothstep(0.0, 0.0, 0.5) == 1.0


def test_solid_paint_takes_the_colour_of_its_mask_zone():
    params = {"g_vColor0": (0.1, 0.1, 0.1, 1.0), "g_vColor1": (0.7, 0.2, 0.2, 1.0), "g_flPaintRoughness": 0.4}
    out = composite.compose(composite.SOLID, params, inputs(), SIZE)
    assert out["colour"].shape == (SIZE, SIZE, 3)
    assert np.allclose(out["colour"], (0.7, 0.2, 0.2), atol=2e-3)
    assert np.allclose(out["roughness"], 0.4) and np.allclose(out["metalness"], 0.0) and np.allclose(out["paint"], 1.0)


def test_unpaintable_areas_keep_the_weapons_own_look():
    bare = inputs(g_tAmbientOcclusion=flat(1.0, 1.0, 1.0, 1.0))
    out = composite.compose(composite.SOLID, {}, bare, SIZE)
    assert np.allclose(out["colour"], (0.2, 0.4, 0.6), atol=2e-3)
    assert np.allclose(out["roughness"], 0.9) and np.allclose(out["metalness"], 0.8) and np.allclose(out["paint"], 0.0)


def test_custom_paint_shows_its_picture_and_wears_off():
    worn_area = inputs(g_tWear=flat(0.5))  # below the level that is scratched even when new
    fresh = composite.compose(composite.CUSTOM, {"g_flPaintMetalness": 0.5}, worn_area, SIZE, wear=0.0)
    assert np.allclose(fresh["colour"], (0.8, 0.5, 0.1), atol=2e-3) and np.allclose(fresh["metalness"], 0.5)
    worn = composite.compose(composite.CUSTOM, {"g_flPaintMetalness": 0.5}, worn_area, SIZE, wear=1.0)
    assert np.allclose(worn["colour"], (0.2, 0.4, 0.6), atol=2e-3) and np.allclose(worn["paint"], 0.0)


def test_roughness_texture_is_used_when_switched_on():
    textures = inputs(g_tPaintRoughness=flat(0.15))
    out = composite.compose(composite.CUSTOM, {"F_ROUGHNESS_TEXTURE": 1}, textures, SIZE)
    assert np.allclose(out["roughness"], 0.15, atol=1e-5)
    out = composite.compose(composite.CUSTOM, {"F_ROUGHNESS_TEXTURE": 0, "g_flPaintRoughness": 0.7}, textures, SIZE)
    assert np.allclose(out["roughness"], 0.7, atol=1e-5)
    textures["g_tPaintMetalness"] = flat(0.85)
    out = composite.compose(composite.CUSTOM, {"F_METALNESS_TEXTURE": 1, "g_flPaintMetalness": 0.1}, textures, SIZE)
    assert np.allclose(out["metalness"], 0.85, atol=1e-5)


@pytest.mark.parametrize("style", range(9))
@pytest.mark.parametrize("separate", (False, True))
def test_every_style_gives_usable_textures_and_wear_never_adds_paint(style, separate):
    rng = np.random.default_rng(style)
    textures = {name: rng.random(image.shape, dtype=np.float32) for name, image in inputs().items()}
    textures["g_tPaintRoughness"] = rng.random((4, 4, 1), dtype=np.float32)
    textures["g_tPaintMetalness"] = rng.random((4, 4, 1), dtype=np.float32)
    params = {"g_flPatternTexCoordScale": 2.0, "g_flPatternTexCoordRotation": 30.0, "g_flWeaponLength1": 30.0, "g_flUvScale1": 0.7, "F_ROUGHNESS_TEXTURE": style % 2, "F_METALNESS_TEXTURE": style % 2}
    left = []
    for wear in (0.0, 0.4, 1.0):
        out = composite.compose(style, params, textures, SIZE, wear, separate)
        for name, channels in (("colour", (SIZE, SIZE, 3)), ("roughness", (SIZE, SIZE)), ("metalness", (SIZE, SIZE)), ("paint", (SIZE, SIZE))):
            assert out[name].shape == channels and out[name].dtype == np.float32
            assert np.isfinite(out[name]).all() and out[name].min() >= 0.0 and out[name].max() <= 1.0
        left.append(float(out["paint"].mean()))
    assert left[0] >= left[1] - 1e-6 >= left[2] - 2e-6


def _ramp_texture():
    """A 2D ramp: top half gold (low alpha), bottom half blue (high alpha)."""
    ramp = np.zeros((4, 4, 4), dtype=np.float32)
    ramp[:2, :, :] = (0.9, 0.75, 0.2, 0.4)
    ramp[2:, :, :] = (0.15, 0.45, 0.95, 0.95)
    return ramp


def test_hemi_oct_maps_flat_normal_to_center_and_tilted_away():
    flat_nrm = flat(0.5, 0.5, 1.0)
    assert np.allclose(composite._hemi_oct(flat_nrm), 0.5, atol=1e-5)
    tilted = flat(1.0, 0.5, 0.5)
    oct_uv = composite._hemi_oct(tilted)
    assert np.allclose(oct_uv, 1.0, atol=1e-5)


@pytest.mark.parametrize("style", (composite.SOLID, composite.ANTIQUED, composite.GUNSMITH))
@pytest.mark.parametrize("trilinear", (0, 1))
def test_case_hardening_ramp_offset_shifts_colour_and_stays_valid(style, trilinear):
    base_tex = inputs(
        g_tMasks=flat(1.0, 1.0, 0.0),
        g_tPattern=flat(0.25, 0.0, 0.0, 1.0),
        g_tCaseHardeningColorRamp=_ramp_texture(),
        g_tNormal=flat(0.5, 0.5, 1.0),
    )
    common = {
        "F_CASE_HARDENING": 1,
        "F_CASE_HARDENING_TRILINEAR": trilinear,
        "g_flCaseHardeningPatternInfluence": 0.5,
        "g_flCaseHardeningGeometricInfluence": 0.0,
        "g_vCaseHardening": (0.0, 1.0, 1.0, 0.0),
        "g_vColor1": (1.0, 1.0, 1.0),
        "g_flPaintRoughness": 0.4,
    }
    gold = composite.compose(style, {**common, "g_flCaseHardeningRampOffset": 0.0}, base_tex, SIZE, wear=0.0, separate_inputs=True)
    blue = composite.compose(style, {**common, "g_flCaseHardeningRampOffset": 1.0}, base_tex, SIZE, wear=0.0, separate_inputs=True)
    for out in (gold, blue):
        for name, shape in (("colour", (SIZE, SIZE, 3)), ("roughness", (SIZE, SIZE)), ("metalness", (SIZE, SIZE)), ("paint", (SIZE, SIZE))):
            assert out[name].shape == shape and np.isfinite(out[name]).all()
            assert out[name].min() >= 0.0 and out[name].max() <= 1.0
    # Ramp offset 0 picks gold (R > B); offset 1 picks blue (B > R).
    assert gold["colour"][..., 0].mean() > gold["colour"][..., 2].mean()
    assert blue["colour"][..., 2].mean() > blue["colour"][..., 0].mean()
    if style in (composite.ANTIQUED, composite.GUNSMITH):
        # Ramp alpha drives metalness on Antiqued and Gunsmith case hardening.
        assert blue["metalness"].mean() > gold["metalness"].mean() + 0.3


def test_solid_case_hardening_reads_pattern_linearly():
    # Horizontal gradient ramp: left = red, right = blue.
    ramp = np.zeros((2, 8, 4), dtype=np.float32)
    ramp[:, :5, :] = (0.9, 0.1, 0.1, 1.0)
    ramp[:, 5:, :] = (0.1, 0.2, 0.9, 1.0)
    # Pattern R = 0.45 -> linear u = 0.5 + 0.45 = 0.95 (blue); sRGB-linearized would be 0.5 + 0.17 = 0.67 (red).
    tex = inputs(
        g_tMasks=flat(0.0, 0.0, 0.0),
        g_tPattern=flat(0.45, 0.0, 0.0, 1.0),
        g_tCaseHardeningColorRamp=ramp,
    )
    params = {
        "F_CASE_HARDENING": 1,
        "g_flCaseHardeningPatternInfluence": 1.0,
        "g_flCaseHardeningGeometricInfluence": 0.0,
        "g_flCaseHardeningRampOffset": 0.0,
        "g_vCaseHardening": (1.0, 0.0, 0.0, 0.0),
    }
    out = composite.compose(composite.SOLID, params, tex, SIZE, wear=0.0, separate_inputs=True)
    assert out["colour"][..., 2].mean() > out["colour"][..., 0].mean()


@pytest.mark.parametrize("style", (composite.SOLID, composite.HYDROGRAPHIC, composite.SPRAY, composite.ANODIZED, composite.ANODIZED_MULTI, composite.ANODIZED_AIR, composite.CUSTOM, composite.ANTIQUED, composite.GUNSMITH))
@pytest.mark.parametrize("blend_mode", range(5))
def test_overlay_blends_across_styles_and_modes(style, blend_mode):
    tex = inputs(
        g_tOverlay=flat(0.2, 0.85, 0.3, 1.0),
        g_tOverlayMask=flat(1.0),
        g_tCaseHardeningColorRamp=_ramp_texture(),
    )
    mask_mode = 8 if style in (composite.CUSTOM, composite.ANTIQUED, composite.GUNSMITH) else 0
    params = {
        "F_OVERLAY_TEXTURE": 1,
        "F_OVERLAY_BLEND_MODE": blend_mode,
        "F_OVERLAY_MASK": mask_mode,
        "g_fOverlayStrength": 1.0,
        "g_fOverlayRoughness": 0.12,
        "g_fOverlayMetalness": 0.88,
        "g_vColor0": (0.8, 0.2, 0.2),
        "g_vColor1": (0.8, 0.2, 0.2),
        "g_flPaintRoughness": 0.7,
    }
    out = composite.compose(style, params, tex, SIZE, wear=0.0, separate_inputs=True)
    for name, shape in (("colour", (SIZE, SIZE, 3)), ("roughness", (SIZE, SIZE)), ("metalness", (SIZE, SIZE)), ("paint", (SIZE, SIZE))):
        assert out[name].shape == shape and np.isfinite(out[name]).all()
        assert out[name].min() >= 0.0 and out[name].max() <= 1.0
    if blend_mode in (0, 4):
        # Normal / Layer blend replaces underlying red with green overlay and overrides roughness/metalness.
        assert out["colour"][..., 1].mean() > out["colour"][..., 0].mean()
        assert np.allclose(out["roughness"], 0.12, atol=2e-2)
        assert np.allclose(out["metalness"], 0.88, atol=2e-2)


def test_masked_spray_and_halftone_and_patina_age():
    # Masked spray: masks G/B override colours 2 and 3.
    tex = inputs(g_tMasks=flat(0.0, 1.0, 0.0), g_tPattern=flat(0.0, 0.0, 0.0, 1.0))
    params = {"F_USE_ALL_MASKS": 1, "g_vColor0": (0.1, 0.1, 0.1), "g_vColor2": (0.2, 0.8, 0.3)}
    out = composite.compose(composite.SPRAY, params, tex, SIZE, wear=0.0, separate_inputs=True)
    assert np.allclose(out["colour"], (0.2, 0.8, 0.3), atol=2e-3)

    # Spraypaint halftone (both cavity modes and threshold directions) produces valid output.
    rng = np.random.default_rng(11)
    ht_tex = {name: rng.random(image.shape, dtype=np.float32) for name, image in inputs().items()}
    for in_cavity, thresholds in ((1, (0.1, 0.2)), (0, (0.3, 0.1))):
        ht = composite.compose(
            composite.SPRAY,
            {
                "F_SPRAYPAINT_HALFTONE": 1,
                "F_USE_ALL_MASKS": 1,
                "g_bHalftoneInCavity": in_cavity,
                "g_vHalftoneThresholds": thresholds,
                "g_vHalftonePatternLevels": (-0.2, 0.3, 0.9),
            },
            ht_tex,
            SIZE,
            wear=0.2,
            separate_inputs=True,
        )
        for name in ("colour", "roughness", "metalness", "paint"):
            assert np.isfinite(ht[name]).all() and ht[name].min() >= 0.0 and ht[name].max() <= 1.0

    # Patina age on Antiqued and Gunsmith shifts appearance with wear and stays in [0, 1].
    pat_tex = inputs(
        g_tPatinaAgeColorRamp=_ramp_texture(),
        g_tPatinaAgeMask=flat(0.0),
        g_tNormal=flat(0.5, 0.5, 1.0),
    )
    for style in (composite.ANTIQUED, composite.GUNSMITH):
        fresh = composite.compose(style, {"F_PATINA_AGE": 1}, pat_tex, SIZE, wear=0.0, separate_inputs=True)
        aged = composite.compose(style, {"F_PATINA_AGE": 1}, pat_tex, SIZE, wear=0.9, separate_inputs=True)
        for out in (fresh, aged):
            for name in ("colour", "roughness", "metalness", "paint"):
                assert np.isfinite(out[name]).all() and out[name].min() >= 0.0 and out[name].max() <= 1.0
        assert not np.allclose(fresh["colour"], aged["colour"])
