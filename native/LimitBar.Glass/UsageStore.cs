using System.Text.Json;
using System.Text.Json.Serialization;

namespace LimitBar.Glass;

public sealed record UsageWindow(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("used_percent")] double? UsedPercent,
    [property: JsonPropertyName("resets_at")] DateTimeOffset? ResetsAt);

public sealed record UsageSnapshot(
    [property: JsonPropertyName("provider_id")] string ProviderId,
    [property: JsonPropertyName("provider_name")] string ProviderName,
    [property: JsonPropertyName("fetched_at")] DateTimeOffset? FetchedAt,
    [property: JsonPropertyName("windows")] List<UsageWindow> Windows);

public static class UsageStore
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = true };

    public static Dictionary<string, UsageSnapshot> Read()
    {
        try
        {
            var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LimitBar", "snapshots.json");
            if (!File.Exists(path)) return DemoIfRequested();
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            return JsonSerializer.Deserialize<Dictionary<string, UsageSnapshot>>(stream, Options) ?? DemoIfRequested();
        }
        catch (IOException) { return new(); }
        catch (JsonException) { return new(); }
    }

    private static Dictionary<string, UsageSnapshot> DemoIfRequested()
    {
        if (Environment.GetEnvironmentVariable("LIMITBAR_DEMO") != "1") return new();
        var now = DateTimeOffset.Now;
        return new()
        {
            ["claude"] = new("claude", "Claude", now, new() { new("five_hour", "5h", 72, now.AddHours(3.7)), new("seven_day", "7d", 61, now.AddDays(4)) }),
            ["codex"] = new("codex", "ChatGPT", now, new() { new("five_hour", "5h", 36, now.AddHours(2.2)), new("seven_day", "7d", 82, now.AddDays(5)) }),
        };
    }
}
