import 'package:flutter/material.dart';

import '../theme.dart';
import '../widgets/common.dart';

/// The first thing someone sees before they have signed in: what Vyterlix is, in a few lines,
/// and a way on. It mirrors the front page of the website.
class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key, required this.onContinue});

  /// Called when the person taps "Log in".
  final VoidCallback onContinue;

  static const _points = <(IconData, String, String)>[
    (
      Icons.insights_outlined,
      'See how you are doing',
      'Sales, customers and money on one page, with a simple health score.',
    ),
    (
      Icons.chat_bubble_outline,
      'Know what changed, and why',
      'Plain English explanations. Or just ask a question about your business.',
    ),
    (
      Icons.task_alt_outlined,
      'Get clear next steps',
      'Early warnings, each with what you could do about it, and a list to follow up.',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    const onDark = Color(0xFFCFE3D6);
    return Scaffold(
      backgroundColor: const Color(0xFF0C1F16),
      bottomNavigationBar: Container(
        color: const Color(0xFF133423),
        child: SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 14, 24, 14),
            child: Center(
              heightFactor: 1,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 480),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        style: FilledButton.styleFrom(
                          backgroundColor: Palette.tea,
                          foregroundColor: const Color(0xFF123222),
                          minimumSize: const Size.fromHeight(52),
                        ),
                        onPressed: onContinue,
                        child: const Text(
                          'Log in',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'New here? Create your account at vyterlix.com, then log in.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Color(0xFF93AA9C), fontSize: 13),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF0C1F16), Color(0xFF133423)],
          ),
        ),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(24, 24, 24, 32),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 480),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const BrandMark(size: 56),
                    const SizedBox(height: 28),
                    const Text(
                      'FOR UK SMALL BUSINESSES',
                      style: TextStyle(
                        color: Palette.tea,
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1.4,
                      ),
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      'Know how your business is really doing. And what to do next.',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 30,
                        height: 1.15,
                        fontWeight: FontWeight.w800,
                        letterSpacing: -0.6,
                      ),
                    ),
                    const SizedBox(height: 16),
                    const Text(
                      'Vyterlix connects the tools you already use, explains what changed in plain English, and turns it into a short list of things to do.',
                      style: TextStyle(
                        color: onDark,
                        fontSize: 16,
                        height: 1.5,
                      ),
                    ),
                    const SizedBox(height: 28),
                    for (final (icon, title, text) in _points) ...[
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Container(
                            width: 42,
                            height: 42,
                            decoration: BoxDecoration(
                              color: Palette.tea,
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Icon(icon, color: const Color(0xFF123222)),
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  title,
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 16,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  text,
                                  style: const TextStyle(
                                    color: onDark,
                                    fontSize: 14,
                                    height: 1.4,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 18),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
