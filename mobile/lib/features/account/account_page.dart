import 'package:flutter/material.dart';
import '../../services/auth_service.dart';

class AccountPage extends StatefulWidget {
  const AccountPage({super.key, required this.auth, this.recovery = false});
  final AuthService auth;
  final bool recovery;
  @override
  State<AccountPage> createState() => _AccountPageState();
}

class _AccountPageState extends State<AccountPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _signup = false, _busy = false, _recovering = false;
  String? _message;
  @override
  void initState() {
    super.initState();
    _recovering = widget.recovery;
  }

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await action();
    } on AuthException catch (error) {
      if (mounted) setState(() => _message = error.message);
    } catch (_) {
      if (mounted) {
        setState(() =>
            _message = 'Could not update your account. Please try again.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    await _run(() async {
      if (_recovering) {
        await widget.auth.updatePassword(_password.text);
        if (mounted) {
          setState(() {
            _recovering = false;
            _message = 'Password updated.';
          });
        }
      } else if (_signup) {
        final message = await widget.auth.signUp(_email.text, _password.text);
        if (mounted) setState(() => _message = message);
      } else {
        await widget.auth.signIn(_email.text, _password.text);
      }
      _password.clear();
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Account')),
        body: SafeArea(
            child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ValueListenableBuilder<AuthSession?>(
            valueListenable: widget.auth.session,
            builder: (context, session, _) => Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (session != null && !_recovering) ...[
                  Text(session.email),
                  const SizedBox(height: 24),
                  FilledButton(
                      onPressed: _busy ? null : () => _run(widget.auth.signOut),
                      child: const Text('Log out')),
                ] else ...[
                  Text(_recovering
                      ? 'Set a new password'
                      : 'Sign in or create an account. You can keep using FindBack as a guest.'),
                  const SizedBox(height: 24),
                  Form(
                      key: _form,
                      child: Column(children: [
                        if (!_recovering)
                          TextFormField(
                              controller: _email,
                              enabled: !_busy,
                              decoration:
                                  const InputDecoration(labelText: 'Email'),
                              keyboardType: TextInputType.emailAddress,
                              autofillHints: const [AutofillHints.email],
                              autocorrect: false,
                              validator: (value) {
                                try {
                                  AuthService.validateEmail(value ?? '');
                                  return null;
                                } on AuthException catch (error) {
                                  return error.message;
                                }
                              }),
                        const SizedBox(height: 16),
                        TextFormField(
                            controller: _password,
                            enabled: !_busy,
                            obscureText: true,
                            decoration: InputDecoration(
                                labelText:
                                    _recovering ? 'New password' : 'Password'),
                            autofillHints: [
                              _signup || _recovering
                                  ? AutofillHints.newPassword
                                  : AutofillHints.password
                            ],
                            validator: (value) {
                              try {
                                AuthService.validatePassword(value ?? '');
                                return null;
                              } on AuthException catch (error) {
                                return error.message;
                              }
                            }),
                      ])),
                  const SizedBox(height: 24),
                  FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: Text(_recovering
                          ? 'Update password'
                          : _signup
                              ? 'Create account'
                              : 'Sign in')),
                  if (!_recovering) ...[
                    TextButton(
                        onPressed: _busy
                            ? null
                            : () => setState(() {
                                  _signup = !_signup;
                                  _message = null;
                                }),
                        child: Text(_signup
                            ? 'Already have an account? Sign in'
                            : 'Create an account')),
                    TextButton(
                        onPressed: _busy
                            ? null
                            : () => _run(() async {
                                  await widget.auth
                                      .sendPasswordReset(_email.text);
                                  if (mounted) {
                                    setState(() => _message =
                                        'If an account exists, check your email for a reset link.');
                                  }
                                }),
                        child: const Text('Forgot password?')),
                  ],
                ],
                if (_busy) const Center(child: CircularProgressIndicator()),
                if (_message != null)
                  Padding(
                      padding: const EdgeInsets.only(top: 16),
                      child:
                          Semantics(liveRegion: true, child: Text(_message!))),
              ],
            ),
          ),
        )),
      );
}
