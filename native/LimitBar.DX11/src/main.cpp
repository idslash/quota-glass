#define _CRT_SECURE_NO_WARNINGS
#include "imgui.h"
#include "imgui_impl_win32.h"
#include "imgui_impl_dx11.h"
#include "../vendor/data/fonts.h"
#include "glass/backdrop.h"
#include "glass/glass.h"
#include <d3d11.h>
#include <dwmapi.h>
#include <windows.h>
#include <windowsx.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <regex>
#include <sstream>
#include <string>
#include <vector>
#include <ctime>

#ifndef WDA_EXCLUDEFROMCAPTURE
#define WDA_EXCLUDEFROMCAPTURE 0x00000011
#endif

static ID3D11Device* g_device = nullptr;
static ID3D11DeviceContext* g_context = nullptr;
static IDXGISwapChain* g_swap = nullptr;
static ID3D11RenderTargetView* g_target = nullptr;
static UINT g_resize_w = 0, g_resize_h = 0;
static Glass::Backdrop g_backdrop;
static Glass::Renderer g_glass;
static bool g_taskbar = false;
static bool g_desktop_parented = false;
static HWND g_desktop_host = nullptr;
static constexpr int kTaskbarWidth = 520;
static constexpr int kTaskbarHeight = 44;
static int g_taskbar_width = kTaskbarWidth;
static int g_taskbar_height = kTaskbarHeight;
static HWND g_hwnd = nullptr;
static ImFont* g_regular = nullptr;
static ImFont* g_semibold = nullptr;
static float g_interface_scale = 1.0f;
static float g_text_scale = 1.0f;
static bool g_dragging = false;
static bool g_force_backdrop_capture = true;
static bool g_allow_capture = false;
static POINT g_drag_cursor{};
static RECT g_drag_window{};

struct Limit { std::string id; double used = -1; std::string reset; };
struct Provider { std::string name; std::vector<Limit> limits; };
struct State { Provider claude{"Claude"}; Provider codex{"ChatGPT"}; };

static bool CreateDevice(HWND hwnd);
static void CreateTarget();
static void CleanupTarget();
static void CleanupDevice();
static void PositionTaskbar();
static void KeepTaskbarVisible();
static void KeepDesktopVisible();
static void ApplyWindowShape(HWND hwnd, bool taskbar);
static void AttachDesktopWindow(HWND hwnd);
static BOOL CALLBACK FindDesktopHost(HWND top, LPARAM);
static State ReadState();
static void DrawDesktop(const State& state, int w, int h, float scale);
static void DrawTaskbar(const State& state, int w, int h, float scale);
static LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);

static ImU32 C(unsigned r, unsigned g, unsigned b, unsigned a = 255) { return IM_COL32(r,g,b,a); }
static std::string ReadAll(const std::wstring& path) {
    std::ifstream f(path, std::ios::binary); if (!f) return {};
    return std::string((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}
static std::wstring CachePath() {
    wchar_t root[MAX_PATH] = {}; DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", root, MAX_PATH);
    return std::wstring(root, n) + L"\\LimitBar\\snapshots.json";
}
static std::string Section(const std::string& json, const char* key) {
    std::string needle = std::string("\"") + key + "\":{";
    size_t p = json.find(needle); if (p == std::string::npos) return {};
    size_t a = json.find("\"windows\":[", p); if (a == std::string::npos) return {};
    a = json.find('[', a); int depth = 0;
    for (size_t i = a; i < json.size(); ++i) { if (json[i]=='[') ++depth; else if (json[i]==']' && --depth==0) return json.substr(a, i-a+1); }
    return {};
}
static Provider ParseProvider(const std::string& json, const char* key, const char* fallback) {
    Provider out{fallback}; std::string s = Section(json, key); if (s.empty()) return out;
    std::regex item(R"re("id":"([^"]+)","label":"[^"]*","used_percent":(null|-?[0-9.]+),"resets_at":(null|"([^"]*)"))re");
    for (std::sregex_iterator it(s.begin(),s.end(),item), end; it!=end; ++it) {
        Limit v; v.id=(*it)[1].str(); if ((*it)[2].str()!="null") v.used=std::stod((*it)[2].str()); v.reset=(*it)[4].str(); out.limits.push_back(v);
    }
    return out;
}
static std::string DemoReset(int minutes) {
    auto future = std::chrono::system_clock::now() + std::chrono::minutes(minutes);
    std::time_t stamp = std::chrono::system_clock::to_time_t(future);
    std::tm utc{}; gmtime_s(&utc, &stamp);
    char value[32] = {};
    std::strftime(value, sizeof(value), "%Y-%m-%dT%H:%M:%S+00:00", &utc);
    return value;
}
static State ReadState() {
    State s; auto json=ReadAll(CachePath());
    if (json.empty() && GetEnvironmentVariableW(L"LIMITBAR_DEMO", nullptr, 0)) {
        s.claude.limits={{"five_hour",72,DemoReset(222)},{"seven_day",61,DemoReset(3780)}};
        s.codex.limits={{"five_hour",36,DemoReset(168)},{"seven_day",82,DemoReset(7560)}}; return s;
    }
    s.claude=ParseProvider(json,"claude","Claude"); s.codex=ParseProvider(json,"codex","ChatGPT"); return s;
}
static const Limit* Primary(const Provider& p) {
    const Limit* best=nullptr; for (auto& x:p.limits) if (x.used>=0 && (!best || x.used>best->used)) best=&x; return best;
}
static ImU32 UsageColor(double used) { return used>=85?C(246,132,148):used>=65?C(242,184,112):C(103,166,255); }
static ImU32 LeftColor(double left) { return left<=15?C(246,145,156):left<=35?C(244,198,130):C(168,224,205); }
static bool HasPrefix(const std::string& value,const char* prefix) { return value.rfind(prefix,0)==0; }
static bool IsEstimated(const Limit& l) { return l.id.find("_estimated")!=std::string::npos; }
static std::string LimitName(const Limit& l) { return HasPrefix(l.id,"five_hour")?"5-hour limit":HasPrefix(l.id,"seven_day")?"Weekly - all models":l.id; }
static bool ParseIsoUtc(const std::string& value, std::chrono::system_clock::time_point& out) {
    if (value.size() < 19) return false;
    std::tm tm{};
    if (std::sscanf(value.c_str(), "%d-%d-%dT%d:%d:%d", &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
                    &tm.tm_hour, &tm.tm_min, &tm.tm_sec) != 6) return false;
    tm.tm_year -= 1900; tm.tm_mon -= 1;
    time_t stamp = _mkgmtime(&tm);
    if (stamp == -1) return false;
    int offsetMinutes = 0;
    if (value.size() >= 25 && (value[19] == '+' || value[19] == '-')) {
        int hours = 0, minutes = 0;
        if (std::sscanf(value.c_str() + 20, "%d:%d", &hours, &minutes) == 2) {
            offsetMinutes = (hours * 60 + minutes) * (value[19] == '+' ? 1 : -1);
        }
    }
    out = std::chrono::system_clock::from_time_t(stamp) - std::chrono::minutes(offsetMinutes);
    return true;
}
static std::string ResetText(const Limit& limit) {
    std::chrono::system_clock::time_point reset;
    if (!ParseIsoUtc(limit.reset, reset)) return "Reset time unavailable";
    auto seconds = std::max<long long>(0, std::chrono::duration_cast<std::chrono::seconds>(reset - std::chrono::system_clock::now()).count());
    char text[64] = {};
    const char* prefix=IsEstimated(limit)?"~Resets":"Resets";
    if (seconds < 60) return std::string(prefix)+" in <1 min";
    if (seconds < 3600) { std::snprintf(text, sizeof(text), "%s in %lld min",prefix, seconds / 60); return text; }
    if (seconds < 86400) { std::snprintf(text, sizeof(text), "%s in %lld hr %lld min",prefix, seconds / 3600, (seconds % 3600) / 60); return text; }
    std::snprintf(text, sizeof(text), "%s in %lld d %lld hr",prefix, seconds / 86400, (seconds % 86400) / 3600); return text;
}
static std::string ResetShort(const Limit& limit) {
    std::chrono::system_clock::time_point reset;
    if (!ParseIsoUtc(limit.reset, reset)) return "reset unavailable";
    auto seconds = std::max<long long>(0, std::chrono::duration_cast<std::chrono::seconds>(reset - std::chrono::system_clock::now()).count());
    char text[48] = {};
    const char* prefix=IsEstimated(limit)?"~":"";
    if (seconds < 60) return std::string(prefix)+"<1m to reset";
    if (seconds < 3600) { std::snprintf(text, sizeof(text), "%s%lldm to reset",prefix, seconds / 60); return text; }
    if (seconds < 86400) { std::snprintf(text, sizeof(text), "%s%lldh %lldm to reset",prefix, seconds / 3600, (seconds % 3600) / 60); return text; }
    std::snprintf(text, sizeof(text), "%s%lldd %lldh to reset",prefix, seconds / 86400, (seconds % 86400) / 3600); return text;
}
static std::string TaskReset(const Limit& limit) {
    std::chrono::system_clock::time_point reset;
    if (!ParseIsoUtc(limit.reset, reset)) return "reset --";
    auto seconds = std::max<long long>(0, std::chrono::duration_cast<std::chrono::seconds>(reset - std::chrono::system_clock::now()).count());
    const char* approx=IsEstimated(limit)?"~":""; char text[40] = {};
    if(seconds<60) return std::string("reset ")+approx+"<1m";
    if(seconds<3600){std::snprintf(text,sizeof(text),"reset %s%lldm",approx,seconds/60);return text;}
    if(seconds<86400){std::snprintf(text,sizeof(text),"reset %s%lldh %lldm",approx,seconds/3600,(seconds%3600)/60);return text;}
    std::snprintf(text,sizeof(text),"reset %s%lldd %lldh",approx,seconds/86400,(seconds%86400)/3600);return text;
}
static std::string Ellipsize(std::string s, float maxw, ImFont* f, float size) {
    if (f->CalcTextSizeA(size,FLT_MAX,0,s.c_str()).x<=maxw) return s;
    while (s.size()>4 && f->CalcTextSizeA(size,FLT_MAX,0,(s+"...").c_str()).x>maxw) s.pop_back(); return s+"...";
}
static void AddText(ImDrawList* d, ImFont* f, float size, ImVec2 p, ImU32 col, const std::string& text) {
    d->AddText(f,size,p,col,text.c_str());
}
static void AddReadableText(ImDrawList* d,ImFont* f,float size,ImVec2 p,ImU32 col,const std::string& text,float scale) {
    d->AddText(f,size,p,col,text.c_str());
}
static void Progress(ImDrawList* d, ImVec2 a, ImVec2 b, double used) {
    d->AddRectFilled(a,b,C(32,40,52,118),(b.y-a.y)*.5f);
    if (used>=0) { float x=a.x+(b.x-a.x)*(float)std::clamp(used,0.0,100.0)/100.f; d->AddRectFilled(a,ImVec2(x,b.y),UsageColor(used),(b.y-a.y)*.5f); }
}
static void SubmitPanel(float w,float h,float inset,float radius) {
    Glass::Primitive p{}; p.cx=w*.5f; p.cy=h*.5f; p.hw=w*.5f-inset; p.hh=h*.5f-inset; p.corner_radius=radius; p.fade=1.f; p.material=Glass::Material::Regular; g_glass.Submit(p);
}
static void SubmitInsetGlass(float x,float y,float w,float h,float scale) {
    Glass::Primitive p{}; p.cx=x+w*.5f; p.cy=y+h*.5f; p.hw=w*.5f; p.hh=h*.5f;
    p.corner_radius=22*scale; p.fade=1.f; p.material=Glass::Material::Thin; g_glass.Submit(p);
}
static void ProviderRows(ImDrawList* d,const Provider& p,float x,float& y,float width,float scale,ImU32 accent) {
    float text=scale*g_text_scale,fs=15*text, font_small=12*text; d->AddCircleFilled(ImVec2(x+6*scale,y+10*scale),5*scale,accent);
    AddText(d,g_semibold,fs,ImVec2(x+16*scale,y),C(246,249,253),p.name); y+=23*scale;
    if (p.limits.empty()) { AddText(d,g_regular,font_small,ImVec2(x,y),C(196,211,226),"Connecting..."); y+=40*scale; return; }
    for (auto& l:p.limits) {
        float top=y; double left=l.used<0?-1:100-l.used; std::string lefts=left<0?"-":std::to_string((int)std::round(left))+"% left";
        AddText(d,g_semibold,14*text,ImVec2(x,top),C(250,252,255),Ellipsize(LimitName(l),width-110*scale,g_semibold,14*text));
        float vw=g_semibold->CalcTextSizeA(14*text,FLT_MAX,0,lefts.c_str()).x; AddText(d,g_semibold,14*text,ImVec2(x+width-vw,top),left<0?C(190,205,220):LeftColor(left),lefts);
        AddText(d,g_regular,font_small,ImVec2(x,top+21*scale),C(198,213,228),ResetText(l));
        std::string used=l.used<0?"-":std::to_string((int)std::round(l.used))+"% used"; float uw=g_regular->CalcTextSizeA(font_small,FLT_MAX,0,used.c_str()).x;
        AddText(d,g_regular,font_small,ImVec2(x+width-uw,top+21*scale),C(198,213,228),used);
        Progress(d,ImVec2(x,top+43*scale),ImVec2(x+width,top+50*scale),l.used); y+=62*scale;
    }
}
static void ProviderCard(ImDrawList* d,const Provider& p,float x,float y,float width,float scale,ImU32 accent) {
    const float text=scale*g_text_scale;
    d->AddCircleFilled(ImVec2(x+27*scale,y+31*scale),6*scale,accent);
    AddText(d,g_semibold,18*text,ImVec2(x+43*scale,y+18*scale),C(252,253,255),p.name);
    const Limit* primary=Primary(p);
    std::string remaining=primary&&primary->used>=0?std::to_string((int)std::round(100-primary->used))+"% LEFT":"CONNECTING";
    float rw=g_semibold->CalcTextSizeA(12*text,FLT_MAX,0,remaining.c_str()).x;
    float remainingX=x+width-20*scale-rw;
    AddReadableText(d,g_semibold,12*text,ImVec2(remainingX,y+21*scale),primary?LeftColor(100-primary->used):C(225,231,239),remaining,scale);
    if(p.limits.empty()) { AddText(d,g_regular,14*text,ImVec2(x+22*scale,y+78*scale),C(214,224,235),"Waiting for usage data..."); return; }
    int index=0;
    for(const auto& l:p.limits) {
        if(index>=2) break;
        float top=y+(57+index*72)*scale;
        std::string pct=l.used<0?"-":std::to_string((int)std::round(l.used))+"%";
        AddText(d,g_semibold,14*text,ImVec2(x+22*scale,top),C(250,252,255),Ellipsize(LimitName(l),width-120*scale,g_semibold,14*text));
        float pw=g_semibold->CalcTextSizeA(18*text,FLT_MAX,0,pct.c_str()).x;
        ImVec2 pctPos(x+width-22*scale-pw,top-3*scale);
        AddReadableText(d,g_semibold,18*text,pctPos,C(248,250,253),pct,scale);
        AddText(d,g_regular,12*text,ImVec2(x+22*scale,top+24*scale),C(218,226,236),Ellipsize(ResetText(l),width-44*scale,g_regular,12*text));
        Progress(d,ImVec2(x+22*scale,top+48*scale),ImVec2(x+width-22*scale,top+55*scale),l.used);
        ++index;
    }
}
static void DrawDesktop(const State& state,int w,int h,float scale) {
    SubmitPanel((float)w,(float)h,1*scale,30*scale);
    const float pad=22*scale, cardW=w-2*pad, cardH=220*scale;
    SubmitInsetGlass(pad,116*scale,cardW,cardH,scale);
    SubmitInsetGlass(pad,356*scale,cardW,cardH,scale);
    auto* d=ImGui::GetBackgroundDrawList(); float text=scale*g_text_scale;
    AddText(d,g_semibold,11*text,ImVec2(28*scale,22*scale),C(206,213,232),"PLAN USAGE");
    AddText(d,g_semibold,30*text,ImVec2(28*scale,43*scale),C(255,255,255),"Claude + ChatGPT");
    AddText(d,g_regular,13*text,ImVec2(28*scale,84*scale),C(205,215,230),"Live limits and reset times");
    ProviderCard(d,state.claude,pad,116*scale,cardW,scale,C(255,185,104));
    ProviderCard(d,state.codex,pad,356*scale,cardW,scale,C(120,181,255));
    AddText(d,g_regular,12*text,ImVec2(28*scale,h-43*scale),C(194,207,223),"Updates automatically");
    AddText(d,g_semibold,11*text,ImVec2(w-116*scale,h-43*scale),C(215,225,240),"LIMITBAR");
}
static void TaskProvider(ImDrawList* d,const Provider& p,float x,float y,float width,float scale,ImU32 accent) {
    const Limit* l=Primary(p); double used=l?l->used:-1, left=used<0?-1:100-used; float text=scale*g_text_scale;
    const float nameSize=14.5f*text,valueSize=15.5f*text,detailSize=10.5f*text;
    d->AddCircleFilled(ImVec2(x+5*scale,y+9*scale),4.2f*scale,accent);
    AddText(d,g_semibold,nameSize,ImVec2(x+15*scale,y-1*scale),C(250,252,255),Ellipsize(p.name,width-78*scale,g_semibold,nameSize));
    std::string v=left<0?"--":std::to_string((int)std::round(left))+"%"; float vw=g_semibold->CalcTextSizeA(valueSize,FLT_MAX,0,v.c_str()).x;
    AddReadableText(d,g_semibold,valueSize,ImVec2(x+width-vw,y-2*scale),left<0?C(210,220,232):LeftColor(left),v,scale);
    std::string detail=l?((HasPrefix(l->id,"five_hour")?"5h  ·  ":"7d  ·  ")+TaskReset(*l)):"connecting";
    AddText(d,g_regular,detailSize,ImVec2(x+15*scale,y+19*scale),C(184,198,214),Ellipsize(detail,width-15*scale,g_regular,detailSize));
    Progress(d,ImVec2(x,y+34*scale),ImVec2(x+width,y+37*scale),used);
}
static void DrawTaskbar(const State& state,int w,int h,float scale) {
    auto* d=ImGui::GetBackgroundDrawList();
    d->AddRectFilled(ImVec2(1,1),ImVec2(w-1.f,h-1.f),C(15,19,27,168),14*scale);
    d->AddRectFilled(ImVec2(14*scale,2*scale),ImVec2(w-14*scale,3*scale),C(255,255,255,11),1*scale);
    float pad=15*scale,gap=34*scale,col=(w-2*pad-gap)/2;
    float y=(h-38*scale)*.5f; TaskProvider(d,state.claude,pad,y,col,scale,C(255,181,101));
    TaskProvider(d,state.codex,pad+col+gap,y,col,scale,C(130,181,255));
}

int main(int argc,char** argv) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    for(int i=1;i<argc;++i){std::string arg=argv[i];if(arg=="--taskbar")g_taskbar=true;else if(arg=="--allow-capture")g_allow_capture=true;else if(arg.rfind("--scale=",0)==0)g_interface_scale=std::clamp(std::stof(arg.substr(8)),1.f,1.5f);else if(arg.rfind("--text-scale=",0)==0)g_text_scale=std::clamp(std::stof(arg.substr(13)),1.f,1.3f);}
    const wchar_t* cls=g_taskbar?L"LimitBarGlassTaskbar":L"LimitBarGlassDesktop";
    HANDLE mutex=CreateMutexW(nullptr,TRUE,g_taskbar?L"Local\\LimitBarGlassTaskbar":L"Local\\LimitBarGlassDesktop");
    if (GetLastError()==ERROR_ALREADY_EXISTS) {
        if (HWND existing=FindWindowW(cls,nullptr)) {
            ShowWindow(existing,SW_SHOW);
            SetWindowPos(existing,g_taskbar?HWND_TOPMOST:HWND_TOP,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_SHOWWINDOW|SWP_NOACTIVATE);
        }
        if(mutex)CloseHandle(mutex); return 0;
    }
    ImGui_ImplWin32_EnableDpiAwareness();
    WNDCLASSEXW wc={sizeof(wc),CS_CLASSDC,WndProc,0,0,GetModuleHandle(nullptr),nullptr,nullptr,nullptr,nullptr,cls,nullptr}; RegisterClassExW(&wc);
    int width=g_taskbar?kTaskbarWidth:(int)std::round(520*g_interface_scale),height=g_taskbar?kTaskbarHeight:(int)std::round(660*g_interface_scale),x=0,y=0;
    if (!g_taskbar) { RECT wa{}; SystemParametersInfoW(SPI_GETWORKAREA,0,&wa,0); x=wa.right-width-24; y=wa.top+24; }
    DWORD ex=WS_EX_LAYERED|WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE|(g_taskbar?WS_EX_TOPMOST:0);
    g_hwnd=CreateWindowExW(ex,cls,L"LimitBar Glass",WS_POPUP,x,y,width,height,nullptr,nullptr,wc.hInstance,nullptr);
    if(!g_taskbar) AttachDesktopWindow(g_hwnd);
    ApplyWindowShape(g_hwnd,g_taskbar);
    SetLayeredWindowAttributes(g_hwnd,RGB(0,0,0),255,LWA_ALPHA); MARGINS margins={-1}; DwmExtendFrameIntoClientArea(g_hwnd,&margins);
    // Keep the overlay hidden until the backdrop owns a clean desktop frame.
    // This is essential for screenshot mode: freezing a frame captured after
    // the overlay appears produces a black exclusion rectangle.
    if (!CreateDevice(g_hwnd)) return 1;
    IMGUI_CHECKVERSION(); ImGui::CreateContext(); ImGuiIO& io=ImGui::GetIO(); io.IniFilename=nullptr; ImGui::StyleColorsDark();
    ImFontConfig fc; fc.FontDataOwnedByAtlas=false; float dpi=ImGui_ImplWin32_GetDpiScaleForHwnd(g_hwnd);
    g_regular=io.Fonts->AddFontFromMemoryTTF(inter_medium.data(),(int)inter_medium.size(),28*dpi,&fc);
    g_semibold=io.Fonts->AddFontFromMemoryTTF(inter_semibold.data(),(int)inter_semibold.size(),30*dpi,&fc); io.FontDefault=g_regular;
    ImGui_ImplWin32_Init(g_hwnd); ImGui_ImplDX11_Init(g_device,g_context);
    if(!g_glass.Init(g_device,g_context)) return 2; Glass::g=&g_glass; g_backdrop.Init(g_device,g_context,g_hwnd);
    bool qaCapture = g_allow_capture || GetEnvironmentVariableW(L"LIMITBAR_ALLOW_CAPTURE", nullptr, 0) > 0;
    ShowWindow(g_hwnd,SW_SHOWNA); UpdateWindow(g_hwnd); if(g_taskbar) PositionTaskbar();
    if (!g_taskbar) KeepDesktopVisible();
    // Bright, refractive liquid glass: a clear centre for legibility with a
    // narrow, luminous blue/violet lens around the perimeter.
    Glass::SetAppearance(0); Glass::SetAccent(.38f,.68f,1.f);
    // The reference only softens the wallpaper by roughly 3.2 px. Keep the
    // renderer on the light blur surface; the shader below owns the tint.
    Glass::SetGlobalMaterial(0.f,.075f,1.10f,28.f,.003f,.62f,.14f);
    for (Glass::Material material : {Glass::Material::Thin, Glass::Material::Regular, Glass::Material::Thick}) {
        auto& p = Glass::EditParams(material);
        p.tint_rgb[0]=.05f; p.tint_rgb[1]=.055f; p.tint_rgb[2]=.10f;
        p.brightness=.04f; p.contrast=1.02f; p.grain=.003f;
        p.inner_shadow=.04f; p.border_intensity=.45f; p.border_width=1.1f; p.sheen=.72f;
    }
    auto& inset=Glass::EditParams(Glass::Material::Thin);
    inset.blur_mix=.02f; inset.saturation=1.14f; inset.refr_strength=42.f; inset.refr_band=50.f;
    inset.chroma=.0045f; inset.tint_opacity=.055f; inset.brightness=.045f;
    inset.border_intensity=.22f; inset.shadow_strength=.055f; inset.shadow_radius=14.f;
    inset.shadow_off_y=3.f;
    Glass::SetLiquidFlow(0.f);
    Glass::GlassEdgeConfig edge;
    edge.fresnel_exp=2.15f; edge.bevel_scale=.48f; edge.lip=.18f;
    edge.specular_exp=54.f; edge.specular_amt=.95f;
    edge.front_amt=1.05f; edge.back_amt=.68f; edge.sat_pop=1.65f;
    edge.bevel_tilt=2.9f; edge.light_height=.78f; edge.edge_shadow=.28f;
    edge.sheen_amt=.86f; edge.grain_amt=.18f; edge.ambient_rim=.46f;
    edge.front_spread=1.15f; edge.back_spread=.92f; edge.smooth_refraction=.72f;
    edge.adapt_strength=.38f; edge.adapt_pivot=.57f; edge.adapt_soft=.20f;
    g_glass.SetEdgeConfig(edge);
    bool done=false; State state; auto lastRead=std::chrono::steady_clock::now()-std::chrono::seconds(5); int frame=0;
    HWND captureForeground=GetForegroundWindow();
    while(!done){ MSG msg; while(PeekMessage(&msg,nullptr,0,0,PM_REMOVE)){TranslateMessage(&msg);DispatchMessage(&msg);if(msg.message==WM_QUIT)done=true;} if(done)break;
        if(g_resize_w&&g_resize_h){CleanupTarget();g_swap->ResizeBuffers(0,g_resize_w,g_resize_h,DXGI_FORMAT_UNKNOWN,0);g_resize_w=g_resize_h=0;CreateTarget();}
        if(g_taskbar){if(frame%60==0)PositionTaskbar();else KeepTaskbarVisible();}
        else if(frame%12==0)KeepDesktopVisible();
        // During a drag the existing full-screen texture is re-sliced at the
        // new window origin. Capturing concurrently makes the backdrop appear
        // to jump a frame behind the window. Refresh once the pointer releases.
        if(qaCapture) {
            // Desktop Duplication can return an empty initial frame while the
            // overlay is hidden. Warm it up while the visible window is still
            // excluded, freeze the last clean frame, then reveal to Snipping
            // Tool. No further captures means no recursive feedback.
            if(frame==0 && g_desktop_parented) ShowWindow(g_hwnd,SW_HIDE);
            if(frame<=30) {
                g_backdrop.Capture();
                if(frame==30) { if(g_desktop_parented) ShowWindow(g_hwnd,SW_SHOWNA); SetWindowDisplayAffinity(g_hwnd, 0); }
            }
            // A frozen clean frame must not become stale when the user
            // minimizes or switches windows. Refresh only on foreground
            // transitions: briefly exclude our overlay, let DWM settle,
            // capture the new desktop, then make the overlay capturable again.
            if(frame>30 && frame%6==0) {
                HWND currentForeground=GetForegroundWindow();
                if(currentForeground!=captureForeground) {
                    if(g_desktop_parented) ShowWindow(g_hwnd,SW_HIDE);
                    SetWindowDisplayAffinity(g_hwnd,WDA_EXCLUDEFROMCAPTURE);
                    DwmFlush(); Sleep(20); g_backdrop.Capture(); DwmFlush();
                    if(g_desktop_parented) ShowWindow(g_hwnd,SW_SHOWNA);
                    SetWindowDisplayAffinity(g_hwnd,0);
                    captureForeground=currentForeground;
                }
            }
        } else if((g_force_backdrop_capture || frame%2==0) && !g_dragging) {
            g_backdrop.Capture(); g_force_backdrop_capture=false;
        }
        auto now=std::chrono::steady_clock::now(); if(now-lastRead>std::chrono::seconds(2)){state=ReadState();lastRead=now;}
        RECT cr{},wr{};GetClientRect(g_hwnd,&cr);GetWindowRect(g_hwnd,&wr);int w=cr.right,h=cr.bottom;
        ImGui_ImplDX11_NewFrame();ImGui_ImplWin32_NewFrame();ImGui::NewFrame();
        g_glass.BeginFrame(w,h,wr.left-g_backdrop.originX(),wr.top-g_backdrop.originY(),g_backdrop.width(),g_backdrop.height(),ImVec2(-1000,-1000));
        // The windows have deliberately fixed physical dimensions.  A layout scale
        // derived from the client rect keeps the typography inside those dimensions
        // on 100/125/150/200% Windows scaling instead of multiplying it twice.
        float uiScale = g_taskbar ? std::min({std::max(dpi,1.2f), w / (float)kTaskbarWidth, h / (float)kTaskbarHeight}) : std::min({dpi, w / 520.f, h / 660.f});
        if(g_taskbar)DrawTaskbar(state,w,h,uiScale);else DrawDesktop(state,w,h,uiScale);
        ImGui::Render();const float clear[4]={0,0,0,0};g_context->OMSetRenderTargets(1,&g_target,nullptr);g_context->ClearRenderTargetView(g_target,clear);
        D3D11_VIEWPORT vp{};vp.Width=(float)w;vp.Height=(float)h;vp.MaxDepth=1;g_context->RSSetViewports(1,&vp);
        g_glass.Render(g_backdrop.heavySRV(),g_backdrop.softSRV());ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());g_swap->Present(1,0);++frame;
    }
    g_backdrop.Shutdown();g_glass.Shutdown();ImGui_ImplDX11_Shutdown();ImGui_ImplWin32_Shutdown();ImGui::DestroyContext();CleanupDevice();DestroyWindow(g_hwnd);UnregisterClassW(cls,wc.hInstance);if(mutex)CloseHandle(mutex);return 0;
}

static void ApplyWindowShape(HWND hwnd,bool taskbar){RECT cr{};GetClientRect(hwnd,&cr);int w=cr.right,h=cr.bottom,r=taskbar?std::max(30,h-18):(int)std::round(58*g_interface_scale);HRGN region=CreateRoundRectRgn(0,0,w+1,h+1,r,r);SetWindowRgn(hwnd,region,TRUE);}
static void AttachDesktopWindow(HWND hwnd){
    HWND progman=FindWindowW(L"Progman",nullptr); if(!progman)return;
    // Keep it as an independent top-level D3D window. Parenting or owning a
    // swap-chain window to Progman makes it disappear when Explorer performs
    // Show Desktop; KeepDesktopVisible manages the z-order instead.
    g_desktop_host=progman;
    g_desktop_parented=false;
}
static BOOL CALLBACK FindDesktopHost(HWND top, LPARAM){
    HWND defview=FindWindowExW(top,nullptr,L"SHELLDLL_DefView",nullptr);
    if(!defview)return TRUE;
    HWND worker=FindWindowExW(nullptr,top,L"WorkerW",nullptr);
    if(worker){g_desktop_host=worker;return FALSE;}
    return TRUE;
}
static void PositionTaskbar(){
    HWND tb=FindWindowW(L"Shell_TrayWnd",nullptr);RECT r{};if(!tb||!GetWindowRect(tb,&r))return;
    RECT tray=r;HWND notify=FindWindowExW(tb,nullptr,L"TrayNotifyWnd",nullptr);if(notify)GetWindowRect(notify,&tray);
    int taskh=r.bottom-r.top;
    int available=(int)(tray.left-r.left-32);
    int width=std::clamp(available,560,640);
    int height=std::clamp(taskh-10,44,60);
    int x=tray.left-width-16;
    int y=r.top+std::max(0,(taskh-height)/2);
    bool resized=width!=g_taskbar_width||height!=g_taskbar_height;
    g_taskbar_width=width;g_taskbar_height=height;
    SetWindowPos(g_hwnd,HWND_TOPMOST,std::max((int)r.left+8,x),y,width,height,SWP_NOACTIVATE|SWP_SHOWWINDOW);
    if(resized)ApplyWindowShape(g_hwnd,true);
}
static void KeepTaskbarVisible(){SetWindowPos(g_hwnd,HWND_TOPMOST,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_NOOWNERZORDER|SWP_SHOWWINDOW);}
static bool DesktopIsForeground(){
    HWND fg=GetForegroundWindow();
    if(!fg)return true;
    wchar_t cls[96]{};
    GetClassNameW(fg,cls,(int)std::size(cls));
    return _wcsicmp(cls,L"Progman")==0 || _wcsicmp(cls,L"WorkerW")==0 ||
           _wcsicmp(cls,L"SHELLDLL_DefView")==0 || _wcsicmp(cls,L"Desktop")==0;
}
static void KeepDesktopVisible(){
    if(IsIconic(g_hwnd))ShowWindowAsync(g_hwnd,SW_RESTORE);
    else if(!IsWindowVisible(g_hwnd))ShowWindowAsync(g_hwnd,SW_SHOWNOACTIVATE);
    // Show Desktop makes the shell the foreground window. In that state the
    // desktop card is temporarily topmost so it is visible above the wallpaper,
    // while ordinary application windows are still allowed to cover it.
    // As soon as the user returns to an app, drop the topmost state again.
    const HWND z=DesktopIsForeground()?HWND_TOPMOST:HWND_NOTOPMOST;
    SetWindowPos(g_hwnd,z,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_NOOWNERZORDER|SWP_SHOWWINDOW);
}
static bool CreateDevice(HWND hwnd){DXGI_SWAP_CHAIN_DESC sd{};sd.BufferCount=2;sd.BufferDesc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;sd.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;sd.OutputWindow=hwnd;sd.SampleDesc.Count=1;sd.Windowed=TRUE;sd.SwapEffect=DXGI_SWAP_EFFECT_DISCARD;D3D_FEATURE_LEVEL fl;D3D_FEATURE_LEVEL levels[]={D3D_FEATURE_LEVEL_11_0,D3D_FEATURE_LEVEL_10_0};HRESULT hr=D3D11CreateDeviceAndSwapChain(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,0,levels,2,D3D11_SDK_VERSION,&sd,&g_swap,&g_device,&fl,&g_context);if(FAILED(hr))return false;CreateTarget();return true;}
static void CreateTarget(){ID3D11Texture2D* b=nullptr;g_swap->GetBuffer(0,IID_PPV_ARGS(&b));g_device->CreateRenderTargetView(b,nullptr,&g_target);b->Release();}
static void CleanupTarget(){if(g_target){g_target->Release();g_target=nullptr;}}
static void CleanupDevice(){CleanupTarget();if(g_swap)g_swap->Release();if(g_context)g_context->Release();if(g_device)g_device->Release();}
extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND,UINT,WPARAM,LPARAM);
static LRESULT CALLBACK WndProc(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp){
    // Desktop dragging is handled before ImGui so its input backend cannot
    // consume the click. SetWindowPos does not enter Windows' modal move loop,
    // which keeps the DXGI backdrop rendering while the card moves.
    if(msg==WM_LBUTTONDOWN&&!g_taskbar&&GET_Y_LPARAM(lp)<112){g_dragging=true;GetCursorPos(&g_drag_cursor);GetWindowRect(hwnd,&g_drag_window);SetCapture(hwnd);return 0;}
    if(msg==WM_MOUSEMOVE&&g_dragging){POINT p{};GetCursorPos(&p);POINT target{g_drag_window.left+p.x-g_drag_cursor.x,g_drag_window.top+p.y-g_drag_cursor.y};HWND parent=GetParent(hwnd);if(parent)MapWindowPoints(HWND_DESKTOP,parent,&target,1);SetWindowPos(hwnd,nullptr,target.x,target.y,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE);return 0;}
    if(msg==WM_LBUTTONUP&&g_dragging){g_dragging=false;g_force_backdrop_capture=true;ReleaseCapture();return 0;}
    if(msg==WM_CAPTURECHANGED){if(g_dragging)g_force_backdrop_capture=true;g_dragging=false;return 0;}
    if(ImGui_ImplWin32_WndProcHandler(hwnd,msg,wp,lp))return true;
    switch(msg){
        case WM_SIZE:if(wp!=SIZE_MINIMIZED){g_resize_w=LOWORD(lp);g_resize_h=HIWORD(lp);}return 0;
        case WM_LBUTTONUP:if(g_taskbar){HWND d=FindWindowW(L"LimitBarGlassDesktop",nullptr);if(d){ShowWindow(d,SW_SHOWNA);SetWindowPos(d,HWND_BOTTOM,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_SHOWWINDOW|SWP_NOACTIVATE);}}return 0;
        case WM_KEYDOWN:if(wp==VK_ESCAPE){ShowWindow(hwnd,SW_HIDE);return 0;}break;
        case WM_DESTROY:PostQuitMessage(0);return 0;
    }
    return DefWindowProcW(hwnd,msg,wp,lp);
}
