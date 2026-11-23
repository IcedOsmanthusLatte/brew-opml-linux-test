package lab.brew.netprobe;

import android.app.Activity;
import android.app.Instrumentation;
import android.os.Bundle;
import android.view.accessibility.AccessibilityNodeInfo;

public final class UiProbe extends Instrumentation {
    private String label;
    private String expectedPackage;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        label = arguments.getString("label", "");
        expectedPackage = arguments.getString("package", "");
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            AccessibilityNodeInfo root = getUiAutomation().getRootInActiveWindow();
            if (root != null && expectedPackage.contentEquals(root.getPackageName())) {
                String state = null;
                for (AccessibilityNodeInfo match : root.findAccessibilityNodeInfosByText(label)) {
                    CharSequence name = match.getText();
                    if (name == null || !label.contentEquals(name)) {
                        continue;
                    }
                    for (AccessibilityNodeInfo current = match; current != null; current = current.getParent()) {
                        CharSequence description = current.getStateDescription();
                        if ("On".contentEquals(description == null ? "" : description)) {
                            state = "on";
                            break;
                        }
                        if ("Off".contentEquals(description == null ? "" : description)) {
                            state = "off";
                            break;
                        }
                        if (current.isCheckable()) {
                            state = current.isChecked() ? "on" : "off";
                            break;
                        }
                    }
                    if (state != null) {
                        break;
                    }
                }
                if (state != null) {
                    result.putString("switch_state", state);
                }
            }
        } catch (RuntimeException error) {
            result.putString("probe_error", error.getClass().getSimpleName());
        }
        finish(Activity.RESULT_OK, result);
    }
}
