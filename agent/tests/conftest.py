import os

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Installed before ft2_agent.app is imported, so setup_tracing() sees a provider and leaves it alone.
os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
os.environ["STUB_MODEL"] = "1"
EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture
def spans():
    EXPORTER.clear()
    yield EXPORTER
    EXPORTER.clear()
