package com.ursmajestic.rush;

import android.content.Context;
import android.os.Bundle;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.print.PrintManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;

/**
 * URS Rush: opens admin.ursmajestic.com/rush. An Android WebView ignores
 * window.print(), so the page calls window.URSPrint.print() instead, which
 * hands the page (with its print CSS: one 80 mm slip per station) to the
 * Android print system -- pick the Bluetooth thermal printer there.
 */
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WebView web = getBridge().getWebView();
        web.addJavascriptInterface(new PrintBridge(web), "URSPrint");
    }

    private class PrintBridge {
        private final WebView web;

        PrintBridge(WebView web) { this.web = web; }

        @JavascriptInterface
        public void print(final String title) {
            runOnUiThread(() -> {
                PrintManager pm = (PrintManager) getSystemService(Context.PRINT_SERVICE);
                String job = (title == null || title.isEmpty()) ? "Rush slips" : title;
                PrintDocumentAdapter adapter = web.createPrintDocumentAdapter(job);
                PrintAttributes attrs = new PrintAttributes.Builder()
                        .setMediaSize(new PrintAttributes.MediaSize("ROLL80", "80 mm roll", 3150, 11690))
                        .setMinMargins(PrintAttributes.Margins.NO_MARGINS)
                        .build();
                pm.print(job, adapter, attrs);
            });
        }
    }
}
