using Microsoft.UI.Xaml;

namespace LimitBar.Glass;

public partial class App : Application
{
    private MainWindow? _window;
    private TaskbarWindow? _taskbar;

    public App() => InitializeComponent();

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        _window.Activate();
        _taskbar = new TaskbarWindow(_window);
        _taskbar.Activate();
        _window.ShowWidget();
    }
}
