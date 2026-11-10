using MARI.Framework.Core.Abstractions;
using MARI.Framework.Core.Abstractions.Attributes;
using MARI.Framework.Core.Abstractions.Enums;
using MARI.Framework.Core.Abstractions.Signals;

namespace MARI.Bridge;

/// <summary>
/// VGrain in charge to write a remediation request file when a door triggers.
/// </summary>
[VGrainIdFormat(IdFormatTypeEnum.String, FirstParameterTemplate = "remediation")]
public interface IRemediationWriterVGrain : IVGrain
{
    Task WriteAsync(SignalMessage signal, IExecutionContext<string> executionContext);
}