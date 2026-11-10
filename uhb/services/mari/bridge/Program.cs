using MARI.Framework;
using MARI.Framework.Builders;
using MARI.Framework.Builders.Signals;
using MARI.Framework.Configurations;
using MARI.Framework.Core.Abstractions.Enums;
using MARI.Framework.Core.Abstractions.Sequence;
using MARI.Framework.Core.Abstractions.Signals;
using MARI.Framework.Core.Abstractions.Triggers;
using MARI.Framework.Node.Abstractions.Configurations;
using MARI.Framework.Node.Abstractions.Models;
using MARI.Framework.Node.Configurations;
using MARI.Toolbox.Abstractions.Enums;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

using System.Threading;

namespace MARI.Bridge;

public static class Program
{
    private static long _signalsEmitted = 0;

    public static void Main(string[] args)
    {
        // ---- Definitions du cas d'usage ----
        var signalA = Signal.Create("rag-slow", "RagSlow");
        var signalB = Signal.Create("cpu-high", "CpuHigh");

        var door = Door.Create("door-a-b", "DoorAB")
                                       .Listen(signalA, signalB)
                                       .UseLogicalAggregator(LogicEnum.And, TimeSpan.FromSeconds(60))
                                       .Build();

                            // New door for fail aggregation: anima-ollama-fail + anima-wazuh-fail
                            var signalOllama = Signal.Create("anima-ollama-fail", "OllamaFail");
                            var signalWazuh = Signal.Create("anima-wazuh-fail", "WazuhFail");

                            var failDoor = Door.Create("anima-fail-door", "AnimaFailDoor")
                                           .Listen(signalOllama, signalWazuh)
                                           .UseLogicalAggregator(LogicEnum.And, TimeSpan.FromSeconds(60))
                                           .Build();

                            var journalSequence = Sequence.Build("write-journal", "WriteJournal")
                                                      .RequiredInput<SignalMessage>()
                                                      .Use<IJournalWriterVGrain>()
                                                      .Configure("journal")
                                                      .Call((a, signal, ctx) => a.WriteAsync(signal, ctx)).Return
                                                      .Build();

                            var remediationSequence = Sequence.Build("write-remediation", "WriteRemediation")
                                                      .RequiredInput<SignalMessage>()
                                                      .Use<IRemediationWriterVGrain>()
                                                      .Configure("remediation")
                                                      .Call((a, signal, ctx) => a.WriteAsync(signal, ctx)).Return
                                                      .Build();

                var doorTrigger = Trigger.Door(door, "door-trigger", "DoorTrigger")
                                         .AddTargetSequence(journalSequence)
                                         .Build();

                var failTrigger = Trigger.Door(failDoor, "fail-trigger", "FailTrigger")
                                         .AddTargetSequence(remediationSequence)
                                         .Build();

        var builder = WebApplication.CreateBuilder(args);
                builder.WebHost.UseUrls("http://127.0.0.1:55553");

                builder.Host.UseMARINode(b =>
                {
                    b.WizardConfig()
                     .SetupCluster(_ => { })
                     .SetupNodeMemories(m => m.UseInMemory(StorageTypeEnum.All))
                     .UseSignals()
                     .ConfigureLogging(l => l.AddConsole())
                     .AddInMemoryDefinitionProvider(m =>
                                          {
                                              m.SetupSignals(signalA, signalB, signalOllama, signalWazuh)
                                               .SetupDoors(door, failDoor)
                                               .SetupTriggers(doorTrigger, failTrigger)
                                               .SetupSequences(journalSequence, remediationSequence);
                                          })
                                          .SetupNodeVGrains(cfg => cfg.Add<IJournalWriterVGrain, JournalWriterVGrain>()
                                                                      .Add<IRemediationWriterVGrain, RemediationWriterVGrain>());
                });

                // Force les ports pinés APRÈS l'auto-configuration MARI/Orleans
                        // (PostConfigure s'exécute en dernier -> garantit silo 55551, gateway 55552)
                        builder.Host.ConfigureServices(s => s.PostConfigure<Orleans.Configuration.EndpointOptions>(o =>
                        {
                            o.SiloPort = 55551;
                            o.GatewayPort = 55552;
                            o.AdvertisedIPAddress = System.Net.IPAddress.Loopback;
                            o.SiloListeningEndpoint = null;
                            o.GatewayListeningEndpoint = null;
                        }));

        var app = builder.Build();

        app.MapGet("/status", () =>
                    Results.Ok(new { status = "ok", signalsEmitted = Interlocked.Read(ref _signalsEmitted) }));

                app.MapGet("/diag", () =>
                {
                    var signalService = app.Services.GetRequiredService<ISignalService>();
                    var results = new List<string>();
                    foreach (var name in new[] { "rag-slow", "cpu-high", "door-a-b", "inconnu-xyz" })
                    {
                        try
                        {
                            var sub = signalService.SubscribeAsync(name, new DiagReceiver(results), CancellationToken.None).GetAwaiter().GetResult();
                            results.Add($"{name}: OK sub={sub}");
                        }
                        catch (Exception ex)
                        {
                            results.Add($"{name}: EX {ex.GetType().Name}: {ex.Message}");
                        }
                    }
                    return Results.Ok(results);
                });

        app.MapPost("/fire", (FireRequest req) =>
                {
                    var signalService = app.Services.GetRequiredService<ISignalService>();
                    Interlocked.Increment(ref _signalsEmitted);
                    // data doit etre null ou un struct (Fire rejette les reference types)
                    signalService.Fire(req.fireId, (object?)null, CancellationToken.None);
                    return Results.Ok(new { status = "signal emitted", fireId = req.fireId, total = Interlocked.Read(ref _signalsEmitted) });
                });

        app.Run();
    }
}

public record FireRequest
{
    public string fireId { get; set; } = "";
    public string sourceId { get; set; } = "hermes";
    public string data { get; set; } = "";
}

public class DiagReceiver : ISignalReceiver, ISignalReceiverReadOnly
{
    private readonly List<string> _log;
    public DiagReceiver(List<string> log) { _log = log; }
    public Task ReceiveSignalAsync(SignalMessage signal)
    {
        _log.Add($"RECEIVED uid={signal.Uid}");
        return Task.CompletedTask;
    }
}
