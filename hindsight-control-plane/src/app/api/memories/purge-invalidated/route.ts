import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const bankId = body.bank_id || request.nextUrl.searchParams.get("bank_id");

    if (!bankId) {
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: "bank_id is required",
          errorKey: "api.errors.validation.bankIdRequired",
        }),
        { status: 400 }
      );
    }

    const payload = body.older_than_days != null ? { older_than_days: body.older_than_days } : {};

    const response = await fetch(dataplaneBankUrl(bankId, `/memories/purge-invalidated`), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const detail = await response.text();
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: detail || `API returned ${response.status}`,
          errorKey: "api.errors.memories.purge",
        }),
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error purging invalidated memories:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to purge invalidated memories",
        errorKey: "api.errors.memories.purge",
      }),
      { status: 500 }
    );
  }
}
