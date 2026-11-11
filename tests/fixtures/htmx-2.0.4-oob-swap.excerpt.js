/* Excerpt from htmx.org@2.0.4 dist/htmx.js (oobSwap) — for test verification only. */
          const beforeSwapDetails = { shouldSwap: true, target, fragment }
          if (!triggerEvent(target, 'htmx:oobBeforeSwap', beforeSwapDetails)) return
          target = beforeSwapDetails.target // allow re-targeting
          if (beforeSwapDetails.shouldSwap) {
            handlePreservedElements(fragment)
            swapWithStyle(swapStyle, target, target, fragment, settleInfo)
            restorePreservedElements()
          }
          forEach(settleInfo.elts, function(elt) {
            triggerEvent(elt, 'htmx:oobAfterSwap', beforeSwapDetails)
          })
