using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using System.Runtime.InteropServices;
using System.Text;
using WinRT.Interop;

namespace LimitBar.Glass;

public sealed partial class TaskbarWindow : Window
{
    private const int WidthPx = 390;
    private const int HeightPx = 40;
    private readonly MainWindow _main;
    private readonly nint _hwnd;
    private readonly AppWindow _appWindow;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(2) };

    public TaskbarWindow(MainWindow main)
    {
        _main = main;
        InitializeComponent();
        SystemBackdrop = new DesktopAcrylicBackdrop();
        _hwnd = WindowNative.GetWindowHandle(this);
        _appWindow = AppWindow.GetFromWindowId(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(_hwnd));
        if (_appWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.SetBorderAndTitleBar(false, false);
            presenter.IsResizable = false;
            presenter.IsMaximizable = false;
            presenter.IsMinimizable = false;
        }
        _appWindow.IsShownInSwitchers = false;
        var exStyle = GetWindowLongPtr(_hwnd, -20).ToInt64();
        SetWindowLongPtr(_hwnd, -20, new nint(exStyle | 0x00000080L | 0x08000000L)); // TOOLWINDOW | NOACTIVATE
        _timer.Tick += (_, _) => { PositionOnTaskbar(); RefreshData(); };
        _timer.Start();
        Activated += (_, _) => { PositionOnTaskbar(); RefreshData(); };
    }

    private void PositionOnTaskbar()
    {
        var taskbar = FindWindow("Shell_TrayWnd", null);
        if (taskbar == 0 || !GetWindowRect(taskbar, out var rect)) return;
        var taskHeight = rect.Bottom - rect.Top;
        if (taskHeight < 20)
        {
            _appWindow.Hide();
            return;
        }
        _appWindow.Show();
        var trayLeft = FindTrayLeft(taskbar, rect.Right - 280);
        var x = Math.Max(rect.Left + 12, trayLeft - WidthPx - 10);
        var y = rect.Top + Math.Max(2, (taskHeight - HeightPx) / 2);
        SetWindowPos(_hwnd, new nint(-1), x, y, WidthPx, Math.Min(HeightPx, taskHeight - 4), 0x0010 | 0x0040);
    }

    private void RefreshData()
    {
        var snapshots = UsageStore.Read();
        Apply(snapshots.GetValueOrDefault("claude"), ClaudeName, ClaudeValue, ClaudeBar, "Claude");
        Apply(snapshots.GetValueOrDefault("codex"), CodexName, CodexValue, CodexBar, "ChatGPT");
    }

    private static void Apply(UsageSnapshot? snapshot, TextBlock name, TextBlock value, ProgressBar bar, string fallback)
    {
        var primary = snapshot?.Windows.Where(w => w.UsedPercent is not null).OrderByDescending(w => w.UsedPercent).FirstOrDefault();
        var used = Math.Clamp(primary?.UsedPercent ?? 0, 0, 100);
        var left = primary?.UsedPercent is null ? null : (int?)Math.Round(100 - used);
        name.Text = snapshot?.ProviderName ?? fallback;
        value.Text = left is null ? "—" : $"{left}%";
        value.Foreground = BrushOf(left is null ? "#FFAAB8C7" : left <= 15 ? "#FFFF6F7D" : left <= 35 ? "#FFFFB85C" : "#FF62E8B1");
        bar.Value = used;
        bar.Foreground = BrushOf(used >= 85 ? "#FFFF6F7D" : used >= 65 ? "#FFFFB85C" : "#FF67A6FF");
    }

    private void GlassSurface_PointerPressed(object sender, PointerRoutedEventArgs e) => _main.ShowWidget();

    private static int FindTrayLeft(nint taskbar, int fallback)
    {
        var result = fallback;
        EnumChildProc callback = (hwnd, _) =>
        {
            var name = new StringBuilder(128);
            GetClassName(hwnd, name, name.Capacity);
            if (name.ToString() is "TrayNotifyWnd" or "SystemTray_Main" or "ControlCenterButton")
            {
                if (GetWindowRect(hwnd, out var child) && child.Left > 0) result = Math.Min(result, child.Left);
            }
            return true;
        };
        EnumChildWindows(taskbar, callback, 0);
        GC.KeepAlive(callback);
        return result;
    }

    private static SolidColorBrush BrushOf(string color) => new((Windows.UI.Color)Microsoft.UI.Xaml.Markup.XamlBindingHelper.ConvertValue(typeof(Windows.UI.Color), color));

    [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
    private delegate bool EnumChildProc(nint hwnd, nint lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern nint FindWindow(string className, string? windowName);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint hwnd, out Rect rect);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(nint hwnd, nint after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(nint parent, EnumChildProc callback, nint lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(nint hwnd, StringBuilder text, int max);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern nint GetWindowLongPtr(nint hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern nint SetWindowLongPtr(nint hwnd, int index, nint value);
}
