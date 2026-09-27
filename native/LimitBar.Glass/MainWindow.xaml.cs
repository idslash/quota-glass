using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using System.Runtime.InteropServices;
using WinRT.Interop;

namespace LimitBar.Glass;

public sealed partial class MainWindow : Window
{
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly nint _hwnd;
    private readonly AppWindow _appWindow;

    public MainWindow()
    {
        InitializeComponent();
        SystemBackdrop = new DesktopAcrylicBackdrop();
        _hwnd = WindowNative.GetWindowHandle(this);
        _appWindow = AppWindow.GetFromWindowId(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(_hwnd));
        _appWindow.Resize(new Windows.Graphics.SizeInt32(440, 510));
        PositionNearTopRight();
        if (_appWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.SetBorderAndTitleBar(false, false);
            presenter.IsResizable = false;
            presenter.IsMaximizable = false;
            presenter.IsMinimizable = false;
        }
        _appWindow.IsShownInSwitchers = false;
        SetTitleBar(DragRegion);
        _timer.Tick += (_, _) => RefreshData();
        _timer.Start();
        Activated += (_, _) => RefreshData();
    }

    public void ShowWidget()
    {
        _appWindow.Show();
        PositionNearTopRight();
        SetWindowPos(_hwnd, new nint(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040);
    }

    private void PositionNearTopRight()
    {
        var area = DisplayArea.GetFromWindowId(_appWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        _appWindow.Move(new Windows.Graphics.PointInt32(area.X + area.Width - 462, area.Y + 22));
    }

    private void RefreshData()
    {
        var snapshots = UsageStore.Read();
        UsageCards.Children.Clear();
        AddProvider(snapshots.GetValueOrDefault("claude"), "Claude", "#FFFFB36B");
        AddProvider(snapshots.GetValueOrDefault("codex"), "ChatGPT", "#FF82B5FF");
        var dates = snapshots.Values.Select(v => v.FetchedAt).Where(v => v is not null).ToArray();
        var newest = dates.Length == 0 ? null : dates.Max();
        UpdatedText.Text = newest is null ? "Waiting for data" : $"Updated {newest.Value.ToLocalTime():HH:mm}";
        LiveDot.Fill = BrushOf(snapshots.Count > 0 ? "#FF62E8B1" : "#FFFFB85C");
    }

    private void AddProvider(UsageSnapshot? snapshot, string fallbackName, string accent)
    {
        var section = new StackPanel { Spacing = 8 };
        var header = new Grid();
        header.ColumnDefinitions.Add(new ColumnDefinition());
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var name = new TextBlock
        {
            Text = snapshot?.ProviderName ?? fallbackName,
            FontSize = 14,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Foreground = BrushOf("#FFF4F8FC"),
            TextTrimming = TextTrimming.CharacterEllipsis,
        };
        header.Children.Add(name);
        var state = new TextBlock { Text = snapshot is null ? "CONNECTING" : "LIVE", FontSize = 10, Foreground = BrushOf(accent), VerticalAlignment = VerticalAlignment.Center };
        Grid.SetColumn(state, 1);
        header.Children.Add(state);
        section.Children.Add(header);

        if (snapshot?.Windows is not { Count: > 0 })
        {
            section.Children.Add(new TextBlock { Text = "No usage data yet", FontSize = 12, Foreground = BrushOf("#FFAAB8C7") });
        }
        else
        {
            foreach (var window in snapshot.Windows.Take(4))
                section.Children.Add(BuildUsageRow(window));
        }
        UsageCards.Children.Add(section);
    }

    private static Border BuildUsageRow(UsageWindow window)
    {
        var used = Math.Clamp(window.UsedPercent ?? 0, 0, 100);
        var remaining = window.UsedPercent is null ? null : (int?)Math.Round(100 - used);
        var panel = new Grid { RowSpacing = 5 };
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        panel.ColumnDefinitions.Add(new ColumnDefinition());
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(92) });

        var label = new TextBlock
        {
            Text = FriendlyLabel(window), FontSize = 12, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Foreground = BrushOf("#FFF5F8FC"), TextTrimming = TextTrimming.CharacterEllipsis,
            MaxLines = 1, Margin = new Thickness(0, 0, 12, 0),
        };
        panel.Children.Add(label);
        var percent = new TextBlock { Text = remaining is null ? "—" : $"{remaining}% left", FontSize = 12, Foreground = BrushOf(RemainingColor(remaining)), HorizontalAlignment = HorizontalAlignment.Right };
        Grid.SetColumn(percent, 1);
        panel.Children.Add(percent);

        var reset = new TextBlock { Text = FormatReset(window.ResetsAt), FontSize = 10, Foreground = BrushOf("#FFA7B6C7"), TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 1 };
        Grid.SetRow(reset, 1);
        panel.Children.Add(reset);
        var usedText = new TextBlock { Text = window.UsedPercent is null ? "—" : $"{Math.Round(used)}% used", FontSize = 10, Foreground = BrushOf("#FFA7B6C7"), HorizontalAlignment = HorizontalAlignment.Right };
        Grid.SetRow(usedText, 1); Grid.SetColumn(usedText, 1);
        panel.Children.Add(usedText);

        var progress = new ProgressBar { Minimum = 0, Maximum = 100, Value = used, Height = 4, Foreground = BrushOf(UsageColor(used)), Background = BrushOf("#382B3A4E") };
        Grid.SetRow(progress, 2); Grid.SetColumnSpan(progress, 2);
        panel.Children.Add(progress);
        return new Border { Background = BrushOf("#24111B29"), BorderBrush = BrushOf("#3AD8ECFF"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(13), Padding = new Thickness(12, 10, 12, 10), Child = panel };
    }

    private static string FriendlyLabel(UsageWindow window) => window.Id switch
    {
        "five_hour" => "5-hour limit",
        "seven_day" => "Weekly · all models",
        _ => window.Label.Length > 36 ? window.Label[..35] + "…" : window.Label,
    };

    private static string FormatReset(DateTimeOffset? value)
    {
        if (value is null) return "Reset —";
        var span = value.Value - DateTimeOffset.Now;
        if (span < TimeSpan.Zero) span = TimeSpan.Zero;
        if (span.TotalHours < 1) return $"Resets in {Math.Max(1, (int)span.TotalMinutes)} min";
        if (span.TotalDays < 1) return $"Resets in {(int)span.TotalHours} hr {span.Minutes} min";
        return $"Resets in {(int)span.TotalDays} d {span.Hours} hr";
    }

    private static string UsageColor(double used) => used >= 85 ? "#FFFF6F7D" : used >= 65 ? "#FFFFB85C" : "#FF67A6FF";
    private static string RemainingColor(int? left) => left is null ? "#FFAAB8C7" : left <= 15 ? "#FFFF6F7D" : left <= 35 ? "#FFFFB85C" : "#FF62E8B1";
    private static SolidColorBrush BrushOf(string color) => new((Windows.UI.Color)Microsoft.UI.Xaml.Markup.XamlBindingHelper.ConvertValue(typeof(Windows.UI.Color), color));

    private void HideButton_Click(object sender, RoutedEventArgs e) => _appWindow.Hide();
    private void RefreshButton_Click(object sender, RoutedEventArgs e) => RefreshData();

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(nint hWnd, nint hWndInsertAfter, int x, int y, int cx, int cy, uint flags);
}
