#pragma once

namespace Glass {

inline constexpr const char* kBlurHLSL = R"HLSL(
cbuffer BlurCB : register(b0)
{
    float2 texel;
    float  spread;
    float  _pad;
};

Texture2D    Src        : register(t0);
SamplerState LinearClamp : register(s0);

struct VSO { float4 pos : SV_Position; float2 uv : TEXCOORD0; };

VSO VSFull(uint id : SV_VertexID)
{
    VSO o;
    float2 uv = float2((id << 1) & 2, id & 2);
    o.uv  = uv;
    o.pos = float4(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0, 0.0, 1.0);
    return o;
}

float4 PSDown(VSO i) : SV_Target
{
    float2 h = texel * (0.5 * spread);
    float4 s = Src.Sample(LinearClamp, i.uv) * 4.0;
    s += Src.Sample(LinearClamp, i.uv + float2(-h.x, -h.y));
    s += Src.Sample(LinearClamp, i.uv + float2( h.x, -h.y));
    s += Src.Sample(LinearClamp, i.uv + float2(-h.x,  h.y));
    s += Src.Sample(LinearClamp, i.uv + float2( h.x,  h.y));
    return s / 8.0;
}

float4 PSUp(VSO i) : SV_Target
{
    float2 h = texel * (0.5 * spread);
    float4 s = Src.Sample(LinearClamp, i.uv + float2(-h.x * 2.0, 0.0));
    s += Src.Sample(LinearClamp, i.uv + float2(-h.x,  h.y)) * 2.0;
    s += Src.Sample(LinearClamp, i.uv + float2( 0.0,  h.y * 2.0));
    s += Src.Sample(LinearClamp, i.uv + float2( h.x,  h.y)) * 2.0;
    s += Src.Sample(LinearClamp, i.uv + float2( h.x * 2.0, 0.0));
    s += Src.Sample(LinearClamp, i.uv + float2( h.x, -h.y)) * 2.0;
    s += Src.Sample(LinearClamp, i.uv + float2( 0.0, -h.y * 2.0));
    s += Src.Sample(LinearClamp, i.uv + float2(-h.x, -h.y)) * 2.0;
    return s / 12.0;
}
)HLSL";

inline constexpr const char* kGlassHLSL = R"HLSL(
cbuffer GlassCB : register(b0)
{
    float2 window_size;
    float2 desktop_size;
    float2 window_origin;
    float2 light_dir;

    float2 widget_center;
    float2 widget_half_size;

    float  corner_radius;
    float  fade;
    float  blur_mix;
    float  saturation;

    float4 tint_color;

    float  tint_opacity;
    float  brightness;
    float  contrast;
    float  grain;

    float  refr_strength;
    float  refr_band;
    float  highlight;
    float  inner_shadow;

    float  border_intensity;
    float  border_width;
    float  shadow_strength;
    float  shadow_radius;

    float2 shadow_offset;
    float  time;
    float  quad_pad;

    float2 tilt;
    float  sheen;
    float  chroma;

    float2 cursor;
    float2 _pad_cur;

    float2 blob1_center;
    float2 blob1_half;
    float2 blob2_center;
    float2 blob2_half;
    float  blob_k;
    float3 _pad_blob;

    float4 edge_p0;
    float4 edge_p1;
    float4 edge_p2;
    float4 edge_p3;
    float4 edge_p4;
    float4 edge_p5;
    float4 edge_p6;
    float4 edge_p7;
    float4 edge_p8;
    float4 edge_p9;
    float4 edge_p10;
    float4 edge_p11;
};

Texture2D    BlurHeavy  : register(t0);
Texture2D    BlurSoft   : register(t1);
SamplerState LinearClamp : register(s0);

struct VSOut {
    float4 pos    : SV_Position;
    float2 local  : TEXCOORD0;
    float2 client : TEXCOORD1;
};

VSOut VS_Glass(uint vid : SV_VertexID)
{
    static const float2 corners[6] = {
        float2(-1,-1), float2( 1,-1), float2(-1, 1),
        float2(-1, 1), float2( 1,-1), float2( 1, 1)
    };
    float2 sgn   = corners[vid];
    float2 local = sgn * (widget_half_size + quad_pad);
    float2 client = widget_center + local;

    float2 ndc;
    ndc.x = (client.x / window_size.x) * 2.0 - 1.0;
    ndc.y = 1.0 - (client.y / window_size.y) * 2.0;

    VSOut o;
    o.pos    = float4(ndc, 0, 1);
    o.local  = local;
    o.client = client;
    return o;
}

float sdRoundedRect(float2 p, float2 hs, float r)
{
    float2 q  = abs(p) - hs + r;
    float2 qm = max(q, 0.0);

    float  n  = max(edge_p8.x, 2.0);
    float  corner = (n <= 2.001) ? length(qm)
                                 : pow(pow(qm.x, n) + pow(qm.y, n), 1.0 / n);
    return min(max(q.x, q.y), 0.0) + corner - r;
}

float hash21(float2 p)
{
    p = frac(p * float2(123.34, 345.45));
    p += dot(p, p + 34.345);
    return frac(p.x * p.y);
}

float3 sampleBackdrop(float2 uv)
{
    float3 h = BlurHeavy.SampleLevel(LinearClamp, uv, 0).rgb;
    float3 s = BlurSoft .SampleLevel(LinearClamp, uv, 0).rgb;
    return lerp(s, h, blur_mix);
}

float3 sampleBackdropFrost(float2 uv, float frost)
{
    float3 h = BlurHeavy.SampleLevel(LinearClamp, uv, 0).rgb;
    float3 s = BlurSoft .SampleLevel(LinearClamp, uv, 0).rgb;
    return lerp(s, h, saturate(blur_mix + frost));
}

float sdScene(float2 p)
{
    if (blob_k > 0.001)
    {
        float dA = sdRoundedRect(p - (blob1_center - widget_center), blob1_half, corner_radius);
        float dB = sdRoundedRect(p - (blob2_center - widget_center), blob2_half, corner_radius);
        float h  = saturate(0.5 + 0.5 * (dB - dA) / blob_k);
        return lerp(dB, dA, h) - blob_k * h * (1.0 - h);
    }
    return sdRoundedRect(p, widget_half_size, corner_radius);
}

float DirLight(float2 L, float2 nrm, float3 N3, float2 np, float fresR, float band)
{
    float front_amt = edge_p1.y, back_amt = edge_p1.z;
    float fSpread   = edge_p4.x, bSpread  = edge_p4.y;
    float specExp   = edge_p0.w, specAmt  = edge_p1.x;
    float lightH    = edge_p2.y, sheenAmt = edge_p3.y;

    float l  = dot(nrm, L);

    float fr = pow(max(0.0,  l), max(1.2, 2.0 / fSpread));
    float bk = pow(max(0.0, -l), max(1.2, 2.0 / bSpread));
    float c  = fresR * highlight * (fr * front_amt + bk * back_amt);

    float3 Lp = normalize(float3(L * 0.9, lightH));
    float3 Hh = normalize(Lp + float3(0.0, 0.0, 1.0));
    c += pow(saturate(dot(N3, Hh)), specExp) * highlight * specAmt;

    float tp = saturate(0.5 + 0.62 * dot(np, L)); tp *= tp;
    float ur = pow(saturate(dot(nrm, L)), 2.0) * band;
    c += (tp * 0.11 + ur * 0.20) * sheen * sheenAmt;
    return c;
}
)HLSL" R"HLSL(
float4 PS_Glass(VSOut i) : SV_Target
{
    float2 hs = widget_half_size;
    float  r  = corner_radius;

    float ecFresExp = edge_p0.x, ecBevel = edge_p0.y, ecLip = edge_p0.z, ecSpecExp = edge_p0.w;
    float ecSpecAmt = edge_p1.x, ecFront = edge_p1.y, ecBack = edge_p1.z, ecSatPop = edge_p1.w;
    float ecBevelTilt = edge_p2.x, ecLightH = edge_p2.y, ecEdgeSh = edge_p2.z, ecCurSize = edge_p2.w;
    float ecCurGlow = edge_p3.x, ecSheen = edge_p3.y, ecGrain = edge_p3.z, ecAmbRim = edge_p3.w;
    float ecFrontSpread = edge_p4.x, ecBackSpread = edge_p4.y;

    float  sizeMin  = min(hs.x, hs.y);
    float  refScale = clamp(44.0 / max(sizeMin, 6.0), 0.35, 1.7);
    float  d  = sdScene(i.local);
    float  aa = max(fwidth(d), 0.75);
    float  distN = saturate(-d / max(min(hs.x, hs.y), 1.0));

    float dS  = sdScene(i.local - shadow_offset);
    float saf = 1.0 - smoothstep(0.0, shadow_radius, dS);
    float shadow_a = saf * saf * shadow_strength;

    if (d > shadow_radius + 2.0 && shadow_a <= 0.003)
        discard;

    float shape = 1.0 - smoothstep(-aa, aa, d);

    const float e = 1.5;
    float dx = sdScene(i.local + float2(e,0)) - sdScene(i.local - float2(e,0));
    float dy = sdScene(i.local + float2(0,e)) - sdScene(i.local - float2(0,e));
    float2 nrm = normalize(float2(dx, dy) + 1e-5);

    float  bevelW = clamp(min(hs.x, hs.y) * ecBevel, 5.0, 24.0);
    float  rimT   = saturate(1.0 - smoothstep(0.0, bevelW, -d));
    float  slope  = rimT * rimT;
    float3 N3     = normalize(float3(nrm * slope * ecBevelTilt, 1.0));
    float  fresR  = pow(saturate(1.0 - N3.z), ecFresExp);

    // Port of sa1emie/liquid-glass-ubersicht's visual model (MIT):
    // broad rim refraction, chromatic edge separation, cool glass tint,
    // inner top glow and a crisp wallpaper-independent specular line.
    float edge = 1.0 - smoothstep(0.0, refr_band, -d);
    float edge2 = edge * edge;
    float2 dir = normalize(widget_center - i.client + float2(1e-4, 1e-4));
    float2 base_uv = (i.client + window_origin) / desktop_size
                   + dir * (edge2 * refr_strength) / desktop_size;

    float2 cdir = dir * edge2 * chroma;
    float3 glass;
    // The soft surface is the native equivalent of the reference's 5x5,
    // 3.2 px kernel. Do not blend in the multi-level heavy frost here.
    glass.r = sampleBackdropFrost(base_uv + cdir, 0.0).r;
    glass.g = sampleBackdropFrost(base_uv,        0.0).g;
    glass.b = sampleBackdropFrost(base_uv - cdir, 0.0).b;

    float lum = dot(glass, float3(0.299, 0.587, 0.114));
    glass = lerp(float3(lum, lum, lum), glass, saturation);
    glass *= 0.99;
    glass = lerp(glass, tint_color.rgb, tint_opacity);
    float glassLum = dot(glass, float3(0.299, 0.587, 0.114));
    float readabilityShade = smoothstep(edge_p7.y-edge_p7.z, edge_p7.y+edge_p7.z, glassLum) * edge_p7.x;
    glass = lerp(glass, tint_color.rgb * 0.72, readabilityShade);

    float2 np = i.local / hs;
    float top = saturate(-np.y * 0.5 + 0.5);
    glass += float3(0.05, 0.05, 0.05) * (1.0 - edge) * pow(top, 1.4);
    float crisp = smoothstep(2.6, 0.0, abs(d));
    glass += crisp * (0.30 + 0.40 * top) * border_intensity;
    glass = lerp(glass, float3(1.0, 1.0, 1.0), brightness);
    glass = saturate(glass);

    float a   = shape * fade;
    float outA = a + shadow_a * (1.0 - a);
    return float4(glass * a, outA);
}
)HLSL";

}
